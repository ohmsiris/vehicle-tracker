"""
Buying for the factory: purchase bills from suppliers, read like the repair bills (app/purchase_extraction.py), and
what every thing bought really cost -- so later we know where to buy it cheapest (the price page), and one day print
purchase orders from here.

* A bill has a supplier (a shop of kind "supplier", kept on the settings page with the words that find it on a bill),
  a date, a number, a VAT mode and a total. Its lines keep the text as written, plus which stock item it is.
* **unit_cost** = what one unit really cost: the bill's total (VAT, a bill discount) is spread over the lines in
  proportion to their amounts, then divided by the quantity. **unit_cost_ex_vat** takes the 7 % back out when the
  bill had VAT. Prices are compared on these, never on the printed unit price.
* A line is matched to a stock item by the item's words (the same longest-word rule as the shops, app/shops.py).
  A line linked by hand teaches the item its text, so the same line on the next bill is found by itself.
* Saving can also put the lines into the stock (app/stock.py) -- off by default while the room is being renovated.

Everything that decides whether a bill is acceptable is a pure function (clean_bill, line_costs, price_stats) and
unit-tested; the database calls are thin. Until schema_v16.sql has been run everything says what is missing.
"""
import hashlib
import logging
import re
import time
import uuid
from datetime import date, timedelta
from typing import Optional

from app import shops, supabase
from app.files import UnsupportedFile, prepare_file

logger = logging.getLogger("vehicle_tracker.purchasing")

VAT_RATE = 0.07
VAT_MODES = ("included", "excluded", "none")
BRANCHES = ["สระบุรี", "แก่งคอย"]
MAX_LINES = 200
MAX_MONEY = 10_000_000
MAX_QTY = 1_000_000
BUDDHIST_YEAR_FLOOR = 2400
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}
NEEDS_V16 = "ต้องรัน schema_v16.sql ใน Supabase ก่อน จึงจะใช้ระบบซื้อของได้"
NEEDS_V30 = "ต้องรัน schema_v30.sql ใน Supabase ก่อน จึงจะบันทึกบิลซ่อมเครื่อง / บิลอ้างอิงได้"
# What a bill is (schema_v30): bought for the store room; a machine's repair (money counts, a repair entry of that machine is made); a parts list kept only to
# know the parts of a machine model -- no money counts anywhere (not in spending, not in the store, not on a machine).
PURPOSES = {"stock": "ซื้อเข้าสต็อก", "repair": "ซ่อมเครื่องจักร", "reference": "รายการอะไหล่อ้างอิง (ไม่นับเงิน)"}


class InvalidPurchase(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class DuplicateBill(Exception):
    """The bill looks already saved: the same file, or the same supplier with the same bill number / the same date and total.
    `message` is Thai and shown to the user before they choose to save it anyway."""
    def __init__(self, found: list[dict], message: str = "ไฟล์บิลนี้เคยบันทึกไว้แล้ว"):
        super().__init__("duplicate bill")
        self.found = found
        self.message = message


class NotFound(Exception):
    pass


# ------------------------------------------------------------------ helpers
def _text(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def _num(value, what: str, *, maximum: float, allow_zero: bool = True) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        n = float(str(value).replace(",", "").strip())
    except ValueError:
        raise InvalidPurchase(f"{what}ต้องเป็นตัวเลข")
    if n < 0 or (n == 0 and not allow_zero) or n > maximum:
        raise InvalidPurchase(f"{what}ไม่ถูกต้อง")
    return n


def _date(value, *, today: date) -> date:
    try:
        d = date.fromisoformat(str(value or "").strip())
    except ValueError:
        raise InvalidPurchase("ใส่วันที่ในบิลด้วย")
    if d.year >= BUDDHIST_YEAR_FLOOR:
        raise InvalidPurchase("ปีเป็น พ.ศ. — ใส่เป็น ค.ศ. (ปีที่แสดงใต้ช่องจะเป็น พ.ศ. ให้เอง)")
    if d > today + timedelta(days=1):
        raise InvalidPurchase("วันที่ในบิลเป็นวันในอนาคต")
    if d.year < 2000:
        raise InvalidPurchase("วันที่ในบิลเก่าเกินไป")
    return d


def _uuid_or_none(value, what: str, known: Optional[set] = None) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        out = str(uuid.UUID(str(value)))
    except ValueError:
        raise InvalidPurchase(f"{what}ไม่ถูกต้อง")
    if known is not None and out not in known:
        raise InvalidPurchase(f"ไม่พบ{what}นี้ (อาจถูกลบไปแล้ว) — โหลดหน้าใหม่แล้วลองอีกครั้ง")
    return out


# ------------------------------------------------------------------ the pure rules
def line_costs(lines: list[dict], vat_mode: str, total: Optional[float]) -> list[dict]:
    """unit_cost / unit_cost_ex_vat for each line (None when the quantity or the amount is missing).

    The bill's total -- what was really paid -- is spread over the lines by their amounts, so VAT added at the bottom
    and a discount on the whole bill are both counted. A total that is far from the lines (a misread, a bill entered
    only in part) is not trusted: then only the VAT is added."""
    amounts = [ln.get("line_total") for ln in lines]
    lines_sum = sum(a for a in amounts if a)
    expected = 1 + VAT_RATE if vat_mode == "excluded" else 1.0
    factor = expected
    if total and lines_sum > 0:
        f = total / lines_sum
        if abs(f - expected) <= 0.25 * expected:
            factor = f
    out = []
    for ln in lines:
        qty, amount = ln.get("quantity"), ln.get("line_total")
        if not qty or amount is None:
            out.append({"unit_cost": None, "unit_cost_ex_vat": None})
            continue
        paid = amount * factor / qty
        ex = paid / (1 + VAT_RATE) if vat_mode in ("included", "excluded") else paid
        out.append({"unit_cost": round(paid, 4), "unit_cost_ex_vat": round(ex, 4)})
    return out


def clean_line(ln: dict, *, items: set, machines: set) -> dict:
    description = _text(ln.get("description"), 300)
    if not description:
        raise InvalidPurchase("ทุกบรรทัดต้องมีชื่อรายการ")
    qty = _num(ln.get("quantity"), "จำนวน", maximum=MAX_QTY, allow_zero=False)
    unit_price = _num(ln.get("unit_price"), "ราคาต่อหน่วย", maximum=MAX_MONEY)
    total = _num(ln.get("line_total"), "ราคารวมของบรรทัด", maximum=MAX_MONEY)
    if total is None and qty and unit_price is not None:
        total = round(qty * unit_price, 2)
    new_item = None
    if isinstance(ln.get("new_item"), dict):
        name = _text(ln["new_item"].get("name"), 120)
        if not name:
            raise InvalidPurchase("ใส่ชื่อรายการใหม่ด้วย")
        new_item = {"name": name, "unit": _text(ln["new_item"].get("unit") or ln.get("unit"), 20) or None,
                    "category": _text(ln["new_item"].get("category"), 40) or None}
    return {
        "description": description, "quantity": qty, "unit": _text(ln.get("unit"), 20) or None,
        "unit_price": unit_price, "line_total": total,
        "item_id": None if new_item else _uuid_or_none(ln.get("item_id"), "รายการของ", items),
        "new_item": new_item, "learn": bool(ln.get("learn")), "unlinked_by_hand": bool(ln.get("unlinked_by_hand")),
        "machine_id": _uuid_or_none(ln.get("machine_id"), "เครื่อง", machines),
        "used_for": _text(ln.get("used_for"), 120) or None,
        "low_confidence_fields": [f for f in (ln.get("low_confidence_fields") or []) if isinstance(f, str)][:10],
    }


def clean_bill(data: dict, *, today: date, vendors: set, items: set, machines: set) -> dict:
    """What the review page sent -> the values to write, or InvalidPurchase."""
    vat_mode = data.get("vat_mode") or "none"
    if vat_mode not in VAT_MODES:
        raise InvalidPurchase("เลือกว่าบิลนี้มี VAT แบบไหน")
    vendor_id = _uuid_or_none(data.get("vendor_id"), "ร้าน", vendors)
    new_vendor = _text(data.get("new_vendor"), 60) or None
    if vendor_id:
        new_vendor = None
    branch = _text(data.get("branch"), 40) or None
    if branch and branch not in BRANCHES:
        raise InvalidPurchase("สาขาไม่ถูกต้อง")
    raw_lines = data.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise InvalidPurchase("ยังไม่มีรายการของในบิล")
    if len(raw_lines) > MAX_LINES:
        raise InvalidPurchase(f"รายการได้ไม่เกิน {MAX_LINES} บรรทัด")
    lines = [clean_line(ln if isinstance(ln, dict) else {}, items=items, machines=machines) for ln in raw_lines]
    total = _num(data.get("total"), "ยอดรวม", maximum=MAX_MONEY)
    if total is None:
        s = sum(ln["line_total"] or 0 for ln in lines)
        total = round(s * (1 + VAT_RATE), 2) if vat_mode == "excluded" else round(s, 2)
    for ln, cost in zip(lines, line_costs(lines, vat_mode, total)):
        ln.update(cost)
    source = "manual" if data.get("source") == "manual" else "bill"
    purpose = data.get("purpose") or "stock"
    if purpose not in PURPOSES:
        raise InvalidPurchase("เลือกว่าบิลนี้คืออะไร (ซื้อเข้าสต็อก / ซ่อมเครื่อง / อ้างอิง)")
    machine_id = _uuid_or_none(data.get("machine_id"), "เครื่อง", machines) if purpose == "repair" else None
    if purpose == "repair" and not machine_id:
        raise InvalidPurchase("บิลซ่อมเครื่อง: เลือกเครื่องที่ซ่อม")
    machine_model = (_text(data.get("machine_model"), 80) or None) if purpose == "reference" else None
    if purpose == "reference" and not machine_model:
        raise InvalidPurchase("บิลอ้างอิง: เลือกรุ่นเครื่องที่รายการนี้เป็นของ")
    if machine_id:
        for ln in lines:                                       # every line of a repair bill is for that machine
            ln["machine_id"] = ln["machine_id"] or machine_id
    return {
        "purpose": purpose, "machine_id": machine_id, "machine_model": machine_model,
        "bill": {
            "vendor_id": vendor_id, "vendor_name": _text(data.get("vendor_name"), 200) or new_vendor, "bill_date": _date(data.get("bill_date"), today=today).isoformat(),
            "bill_no": _text(data.get("bill_no"), 60) or None, "branch": branch, "vat_mode": vat_mode,
            "subtotal": _num(data.get("subtotal"), "ยอดก่อน VAT", maximum=MAX_MONEY), "vat_amount": _num(data.get("vat_amount"), "VAT", maximum=MAX_MONEY),
            "discount": _num(data.get("discount"), "ส่วนลด", maximum=MAX_MONEY), "total": total,
            "notes": _text(data.get("notes"), 500) or None, "source": source,
        },
        "new_vendor": new_vendor, "lines": lines, "receive_stock": bool(data.get("receive_stock")) and purpose == "stock",
    }


def match_item(description: Optional[str], items: list[dict]) -> Optional[dict]:
    """The stock item a bill line means, by the item's words and name (longest wins), or None."""
    hit = shops.explain(description, [{"name": i["name"], "aliases": i.get("aliases", [])} for i in items])
    if not hit["matched"]:
        return None
    item = next((i for i in items if i["name"] == hit["shop"]), None)
    return {"id": item["id"], "name": item["name"], "word": hit["word"]} if item else None


def price_stats(rows: list[dict]) -> dict:
    """rows = the purchase lines of ONE item, each with bill_date, vendor, unit_cost, unit_cost_ex_vat, quantity, unit.
    -> last / lowest / per-supplier latest, the newest first."""
    priced = sorted([r for r in rows if r.get("unit_cost") is not None], key=lambda r: (r["bill_date"], r.get("created_at") or ""), reverse=True)
    if not priced:
        return {"count": len(rows), "last": None, "lowest": None, "by_vendor": []}
    lowest = min(priced, key=lambda r: r["unit_cost"])
    by_vendor: dict = {}
    for r in priced:
        v = r.get("vendor") or "ไม่ระบุร้าน"
        if v not in by_vendor:
            by_vendor[v] = {"vendor": v, "last_cost": r["unit_cost"], "last_cost_ex_vat": r.get("unit_cost_ex_vat"), "last_date": r["bill_date"],
                            "lowest_cost": r["unit_cost"], "times": 0}
        by_vendor[v]["times"] += 1
        by_vendor[v]["lowest_cost"] = min(by_vendor[v]["lowest_cost"], r["unit_cost"])
    pick = lambda r: {k: r.get(k) for k in ("unit_cost", "unit_cost_ex_vat", "bill_date", "vendor", "quantity", "unit", "bill_id")}
    return {"count": len(rows), "last": pick(priced[0]), "lowest": pick(lowest),
            "by_vendor": sorted(by_vendor.values(), key=lambda v: v["last_cost"])}


# ------------------------------------------------------------------ database
_probe = {"at": 0.0, "ok": False}


def has_v16() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        for table in ("purchase_bills", "purchase_lines", "purchase_attachments", "stock_items", "stock_item_aliases", "stock_moves"):
            supabase.select(table, {"select": "id", "limit": "1"})
        supabase.select("shops", {"select": "kind", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not has_v16():
        raise InvalidPurchase(NEEDS_V16)


def today_th() -> date:
    from datetime import datetime, timezone
    return (datetime.now(timezone.utc) + timedelta(hours=7)).date()


def items_with_aliases() -> list[dict]:
    rows = supabase.select_all("stock_items", {"select": "*", "order": "name.asc"})
    aliases = supabase.select_all("stock_item_aliases", {"select": "item_id,alias", "order": "alias.asc"})
    by: dict = {}
    for a in aliases:
        by.setdefault(a["item_id"], []).append(a["alias"])
    return [{**r, "aliases": by.get(r["id"], [])} for r in rows]


def _machines() -> list[dict]:
    try:
        return supabase.select_all("machines", {"select": "id,name,branch,active,make,model", "order": "name.asc"})
    except supabase.SupabaseError:
        return []


def machine_models(machines: list[dict]) -> list[str]:
    """The machine models to choose from for a reference bill: "MYCOM 8WB-HE" (make + model), each once."""
    out: dict = {}
    for m in machines:
        if m.get("model"):
            name = " ".join(x for x in (m.get("make"), m.get("model")) if x).strip()
            out.setdefault(name.lower(), name)
    return sorted(out.values())


def model_matches(machine: dict, model_text: Optional[str]) -> bool:
    """A reference bill's model is this machine's: its model code is in the text (spaces, dashes and capitals do not matter)."""
    code = re.sub(r"[\s\-_.]", "", str(machine.get("model") or "")).lower()
    return bool(code) and code in re.sub(r"[\s\-_.]", "", str(model_text or "")).lower()


_probe30 = {"at": 0.0, "ok": False}


def has_v30() -> bool:
    now = time.time()
    if now - _probe30["at"] < 30:
        return _probe30["ok"]
    try:
        supabase.select("purchase_bills", {"select": "purpose,machine_id,machine_model", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe30.update(at=now, ok=ok)
    return ok


def options() -> dict:
    ready = supabase.configured() and has_v16()
    if not ready:
        return {"ready": False, "suppliers": [], "items": [], "machines": [], "branches": BRANCHES}
    suppliers = [{"id": s["id"], "name": s["name"], "active": s.get("active", True)} for s in shops.of_kind("supplier", shops._load())]
    machines = _machines()
    return {"ready": True, "suppliers": suppliers, "items": items_with_aliases(), "machines": machines, "branches": BRANCHES,
            "purposes": PURPOSES, "machine_models": machine_models(machines), "purpose_ready": has_v30()}


def draft_from_extraction(ex: dict, items: list[dict]) -> dict:
    """The reader's output -> what the review page shows: the supplier and every line already matched where we can."""
    supplier = shops.find_supplier(ex.get("vendor_name"))
    lines = []
    for ln in ex.get("line_items") or []:
        m = match_item(ln.get("description"), items)
        lines.append({**ln, "item_id": m["id"] if m else None, "item_match": m})
    return {**ex, "vendor_id": supplier["id"] if supplier else None, "vendor_match": supplier["name"] if supplier else None, "line_items": lines}


def _create_supplier(name: str) -> str:
    existing = next((s for s in shops.of_kind("supplier", shops._load()) if shops._norm(s["name"]) == shops._norm(name)), None)
    if existing:
        return existing["id"]
    try:
        shops.create({"name": name, "kind": "supplier"})
    except shops.InvalidShop as exc:
        raise InvalidPurchase(f"เพิ่มร้านใหม่ไม่ได้: {exc}")
    found = next(s for s in shops._load() if shops._norm(s["name"]) == shops._norm(name))
    return found["id"]


def _create_item(new: dict, by_name: dict) -> str:
    key = new["name"].lower()
    if key in by_name:
        return by_name[key]
    row = supabase.insert("stock_items", [{"name": new["name"], "unit": new.get("unit"), "category": new.get("category")}])[0]
    by_name[key] = row["id"]
    return row["id"]


def _learn(item_id: str, description: str, taken: set) -> None:
    """A line linked by hand: its text becomes one of the item's words (unless another item has it already)."""
    word = _text(description, 120)
    if not word or word.lower() in taken:
        return
    supabase.insert("stock_item_aliases", [{"item_id": item_id, "alias": word}])
    taken.add(word.lower())


def _bill_key(text) -> str:
    return re.sub(r"[\s\-_./]", "", str(text or "")).lower()


def same_bill_matches(bill: dict, existing: list[dict]) -> list[dict]:
    """The saved bills this one probably is: the same supplier (id, or the name when there is no id) AND either the same bill number
    or the same date with the same total. Pure."""
    def same_vendor(b):
        if bill.get("vendor_id") and b.get("vendor_id"):
            return bill["vendor_id"] == b["vendor_id"]
        return bool(bill.get("vendor_name")) and _bill_key(bill["vendor_name"]) == _bill_key(b.get("vendor_name"))
    out = []
    for b in existing:
        if not same_vendor(b):
            continue
        if _bill_key(bill.get("bill_no")) and _bill_key(bill.get("bill_no")) == _bill_key(b.get("bill_no")):
            reason = "เลขที่บิลเดียวกัน"
        elif bill.get("total") is not None and b.get("total") is not None and str(b.get("bill_date"))[:10] == str(bill.get("bill_date"))[:10] and abs(float(b["total"]) - float(bill["total"])) < 0.005:
            reason = "วันที่และยอดรวมเท่ากัน"
        else:
            continue
        out.append({"bill_id": b["id"], "bill_date": b.get("bill_date"), "bill_no": b.get("bill_no"), "total": b.get("total"), "reason": reason})
    return out


def _find_same_bill(bill: dict) -> list[dict]:
    rows: dict = {}
    queries = [{"bill_date": f"eq.{bill['bill_date']}"}]
    if _bill_key(bill.get("bill_no")):
        queries.append({"bill_no": f"eq.{bill['bill_no']}"})
    for q in queries:
        for r in supabase.select("purchase_bills", {"select": "id,vendor_id,vendor_name,bill_date,bill_no,total", **q}):
            rows[r["id"]] = r
    return same_bill_matches(bill, list(rows.values()))


def save_bill(payload: dict, files: list[tuple[str, bytes]], *, allow_duplicates: bool = False, extraction_model: Optional[str] = None) -> dict:
    _require()
    items = items_with_aliases()
    suppliers = shops.of_kind("supplier", shops._load())
    clean = clean_bill(payload, today=today_th(), vendors={s["id"] for s in suppliers}, items={i["id"] for i in items},
                       machines={m["id"] for m in _machines()})
    prepared = []
    for i, (name, raw) in enumerate(files):
        try:
            data, mime = prepare_file(raw)
        except UnsupportedFile as exc:
            raise InvalidPurchase(f"{name}: {exc}")
        prepared.append({"filename": name, "data": data, "mime": mime, "sha256": hashlib.sha256(data).hexdigest(), "sort_order": i})
    if prepared and not allow_duplicates:
        hashes = ",".join(p["sha256"] for p in prepared)
        found = supabase.select("purchase_attachments", {"select": "bill_id,sha256,created_at", "sha256": f"in.({hashes})"})
        if found:
            raise DuplicateBill(found)
    if not allow_duplicates:
        same = _find_same_bill(clean["bill"])
        if same:
            first = same[0]
            raise DuplicateBill(same, f"บิลนี้น่าจะเคยบันทึกไว้แล้ว ({first['reason']}"
                                      f"{' · ' + str(first['bill_no']) if first.get('bill_no') else ''} · {first['bill_date']})")

    bill = dict(clean["bill"])
    if clean["purpose"] != "stock" and not has_v30():
        raise InvalidPurchase(NEEDS_V30)
    if has_v30():
        bill.update(purpose=clean["purpose"], machine_id=clean["machine_id"], machine_model=clean["machine_model"])
    if clean["new_vendor"]:
        bill["vendor_id"] = _create_supplier(clean["new_vendor"])
    if payload.get("extraction") and isinstance(payload["extraction"], dict):
        bill["extraction"] = payload["extraction"]
        bill["extraction_model"] = extraction_model
    row = supabase.insert("purchase_bills", [bill])[0]
    bill_id = row["id"]
    uploaded: list[str] = []
    try:
        for p in prepared:
            path = f"purchases/{bill_id}/{p['sort_order'] + 1}.{EXTENSIONS.get(p['mime'], 'bin')}"
            supabase.storage_upload(path, p["data"], p["mime"])
            uploaded.append(path)
            supabase.insert("purchase_attachments", [{"bill_id": bill_id, "storage_path": path, "mime_type": p["mime"], "byte_size": len(p["data"]),
                                                      "sha256": p["sha256"] if not allow_duplicates else f"{p['sha256']}:{bill_id}", "sort_order": p["sort_order"]}])
        by_name = {i["name"].lower(): i["id"] for i in items}
        taken = {a.lower() for i in items for a in i["aliases"]}
        rows = []
        for n, ln in enumerate(clean["lines"]):
            item_id = _create_item(ln["new_item"], by_name) if ln["new_item"] else ln["item_id"]
            if not item_id and not ln["new_item"] and not ln["unlinked_by_hand"]:
                hit = match_item(ln["description"], items)          # a line typed in by hand finds its item like a read one
                item_id = hit["id"] if hit else None
            if item_id and (ln["learn"] or ln["new_item"]):
                _learn(item_id, ln["description"], taken)
            rows.append({"bill_id": bill_id, "line_number": n + 1, "description": ln["description"], "item_id": item_id, "quantity": ln["quantity"],
                         "unit": ln["unit"], "unit_price": ln["unit_price"], "line_total": ln["line_total"], "unit_cost": ln["unit_cost"],
                         "unit_cost_ex_vat": ln["unit_cost_ex_vat"], "machine_id": ln["machine_id"], "used_for": ln["used_for"],
                         "low_confidence_fields": ln["low_confidence_fields"]})
        saved = supabase.insert("purchase_lines", rows)
        if clean["receive_stock"]:
            moves = [{"item_id": s["item_id"], "move_date": bill["bill_date"], "kind": "in", "qty": s["quantity"], "purchase_line_id": s["id"],
                      "notes": f"จากบิล {bill.get('bill_no') or ''}".strip()} for s in saved if s.get("item_id") and s.get("quantity")]
            if moves:
                supabase.insert("stock_moves", moves)
        if clean["purpose"] == "repair":                          # the machine's history gets the repair (deleted with the bill)
            machine = next((m for m in _machines() if m["id"] == clean["machine_id"]), {})
            vendor = bill.get("vendor_name") or next((s["name"] for s in suppliers if s["id"] == bill.get("vendor_id")), "") or "ไม่ระบุร้าน"
            supabase.insert("machine_events", [{
                "branch": bill.get("branch") or machine.get("branch") or BRANCHES[0], "machine_id": clean["machine_id"], "event_date": bill["bill_date"], "kind": "ซ่อม",
                "summary": f"ซ่อม / อะไหล่จาก {vendor} ({len(saved)} รายการ)"[:300], "vendor": vendor[:200], "cost": bill.get("total"),
                "notes": f"จากบิลซื้อของ{' เลขที่ ' + bill['bill_no'] if bill.get('bill_no') else ''}", "purchase_bill_id": bill_id}])
    except Exception:
        supabase.delete("purchase_bills", {"id": f"eq.{bill_id}"})          # the lines and attachments go with it
        supabase.storage_delete(uploaded)
        raise
    return {"bill_id": bill_id}


def _vendor_names() -> dict:
    return {s["id"]: s["name"] for s in shops._load()}


def list_bills(limit: int = 100) -> dict:
    if not (supabase.configured() and has_v16()):
        return {"ready": False, "bills": []}
    bills = supabase.select("purchase_bills", {"select": "id,vendor_id,vendor_name,bill_date,bill_no,branch,total,vat_mode,source,created_at,purchase_lines(count)"
                                               + (",purpose,machine_id,machine_model" if has_v30() else ""),
                                               "order": "bill_date.desc,created_at.desc", "limit": str(max(1, min(500, limit)))})
    names = _vendor_names()
    machines = {m["id"]: m["name"] for m in _machines()}
    return {"ready": True, "bills": [{**b, "vendor": names.get(b.get("vendor_id")) or b.get("vendor_name"), "purpose": b.get("purpose") or "stock",
                                      "machine_name": machines.get(b.get("machine_id")),
                                      "lines": (b.pop("purchase_lines", None) or [{"count": 0}])[0]["count"]} for b in bills], "purposes": PURPOSES}


def bill_detail(bill_id: str) -> dict:
    _require()
    _uuid_or_none(bill_id, "บิล")
    rows = supabase.select("purchase_bills", {"select": "*", "id": f"eq.{bill_id}"})
    if not rows:
        raise NotFound()
    b = rows[0]
    lines = supabase.select_all("purchase_lines", {"select": "*", "bill_id": f"eq.{bill_id}", "order": "line_number.asc"})
    atts = supabase.select_all("purchase_attachments", {"select": "*", "bill_id": f"eq.{bill_id}", "order": "sort_order.asc"})
    items = {i["id"]: i["name"] for i in supabase.select_all("stock_items", {"select": "id,name", "order": "name.asc"})}
    machines = {m["id"]: m["name"] for m in _machines()}
    b.pop("extraction", None)
    return {**b, "vendor": _vendor_names().get(b.get("vendor_id")) or b.get("vendor_name"), "purpose": b.get("purpose") or "stock", "machine_name": machines.get(b.get("machine_id")),
            "lines": [{**ln, "item_name": items.get(ln.get("item_id")), "machine_name": machines.get(ln.get("machine_id"))} for ln in lines],
            "attachments": [{"id": a["id"], "mime_type": a["mime_type"], "url": supabase.public_url(a["storage_path"])} for a in atts]}


def machine_prices(machine_id: str) -> dict:
    """Every price and part known for one machine: its repair bills, the lines of other bills marked for it, and the reference parts lists of its model
    (those cost nothing: they are prices seen, not money spent). Newest first."""
    _require()
    _uuid_or_none(machine_id, "เครื่อง")
    machine = next((m for m in _machines() if m["id"] == machine_id), None)
    if not machine:
        raise NotFound()
    v30 = has_v30()
    cols = "id,vendor_id,vendor_name,bill_date,bill_no,total" + (",purpose,machine_id,machine_model" if v30 else "")
    bills = {b["id"]: b for b in supabase.select_all("purchase_bills", {"select": cols, "order": "bill_date.desc,id.asc"})}
    mine = {bid for bid, b in bills.items() if (b.get("purpose") == "repair" and b.get("machine_id") == machine_id)
            or (b.get("purpose") == "reference" and model_matches(machine, b.get("machine_model")))}
    lines = supabase.select_all("purchase_lines", {"select": "id,bill_id,line_number,description,quantity,unit,unit_cost,line_total,machine_id", "order": "line_number.asc"})
    names = _vendor_names()
    rows = []
    for ln in lines:
        b = bills.get(ln["bill_id"])
        if not b or not (ln["bill_id"] in mine or ln.get("machine_id") == machine_id):
            continue
        purpose = b.get("purpose") or "stock"
        rows.append({"bill_id": b["id"], "bill_date": b["bill_date"], "bill_no": b.get("bill_no"), "vendor": names.get(b.get("vendor_id")) or b.get("vendor_name"),
                     "purpose": purpose, "purpose_label": PURPOSES[purpose], "description": ln["description"], "quantity": ln.get("quantity"), "unit": ln.get("unit"),
                     "unit_cost": ln.get("unit_cost"), "line_total": ln.get("line_total"), "counts": purpose != "reference"})
    rows.sort(key=lambda r: (str(r["bill_date"]), r["description"]), reverse=True)
    spent = round(sum(float(r["line_total"] or 0) for r in rows if r["counts"]), 2)
    return {"machine": {"id": machine["id"], "name": machine["name"], "make": machine.get("make"), "model": machine.get("model")}, "rows": rows,
            "spent": spent, "bills": len({r["bill_id"] for r in rows}), "reference_lines": sum(1 for r in rows if not r["counts"]), "ready": v30}


def delete_bill(bill_id: str) -> None:
    _require()
    _uuid_or_none(bill_id, "บิล")
    atts = supabase.select_all("purchase_attachments", {"select": "storage_path", "bill_id": f"eq.{bill_id}", "order": "sort_order.asc"})
    lines = supabase.select_all("purchase_lines", {"select": "id", "bill_id": f"eq.{bill_id}", "order": "line_number.asc"})
    if lines:                                                       # what this bill put on the shelf comes off again
        supabase.delete("stock_moves", {"purchase_line_id": f"in.({','.join(l['id'] for l in lines)})"})
    supabase.delete("purchase_bills", {"id": f"eq.{bill_id}"})
    supabase.storage_delete([a["storage_path"] for a in atts])


def link_line(line_id: str, data: dict) -> dict:
    """The price page: say which item an unlinked line is (or make a new item from it); it teaches the item the text."""
    _require()
    _uuid_or_none(line_id, "บรรทัด")
    rows = supabase.select("purchase_lines", {"select": "id,description,unit", "id": f"eq.{line_id}"})
    if not rows:
        raise NotFound()
    ln = rows[0]
    items = items_with_aliases()
    if isinstance(data.get("new_item"), dict):
        name = _text(data["new_item"].get("name"), 120)
        if not name:
            raise InvalidPurchase("ใส่ชื่อรายการใหม่ด้วย")
        item_id = _create_item({"name": name, "unit": ln.get("unit"), "category": _text(data["new_item"].get("category"), 40) or None},
                               {i["name"].lower(): i["id"] for i in items})
    else:
        item_id = _uuid_or_none(data.get("item_id"), "รายการของ", {i["id"] for i in items})
    supabase.update("purchase_lines", {"id": f"eq.{line_id}"}, {"item_id": item_id})
    if item_id:
        _learn(item_id, ln["description"], {a.lower() for i in items for a in i["aliases"]})
    return prices()


def prices(q: str = "") -> dict:
    """Every item with what it cost where, and the lines not linked to an item yet."""
    if not (supabase.configured() and has_v16()):
        return {"ready": False, "items": [], "unlinked": []}
    lines = supabase.select_all("purchase_lines", {"select": "id,bill_id,description,item_id,quantity,unit,unit_cost,unit_cost_ex_vat,created_at,"
                                                             "purchase_bills(bill_date,vendor_id,vendor_name,bill_no)", "order": "created_at.desc"})
    names = _vendor_names()
    flat = []
    for ln in lines:
        b = ln.pop("purchase_bills", None) or {}
        flat.append({**ln, "bill_date": b.get("bill_date"), "bill_no": b.get("bill_no"), "vendor": names.get(b.get("vendor_id")) or b.get("vendor_name")})
    items = supabase.select_all("stock_items", {"select": "id,name,unit,category,spec", "order": "name.asc"})
    needle = _text(q, 80).lower()
    out = []
    for it in items:
        rows = [r for r in flat if r["item_id"] == it["id"]]
        if needle and needle not in " ".join([it["name"], it.get("category") or "", it.get("spec") or ""] + [r["description"] for r in rows] + [r["vendor"] or "" for r in rows]).lower():
            continue
        out.append({**it, **price_stats(rows), "history": rows[:30]})
    unlinked = [r for r in flat if not r["item_id"] and (not needle or needle in f"{r['description']} {r['vendor'] or ''}".lower())]
    return {"ready": True, "items": out, "unlinked": unlinked[:200]}
