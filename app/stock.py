"""
The mechanics' stock room -- a first draft (the room is being renovated): the things kept (stock_items, the same list
the purchase bills link their lines to), and every move in or out (stock_moves). What is on the shelf is the sum of the
moves; nothing is stored twice.

  in     + received (by hand, or from a purchase bill when "รับเข้าสต็อก" is ticked)
  out    - taken for a job (optionally which machine)
  count  a stock-take: the move that brings the shelf to what was counted (so the history explains every change)

How the room is run: IN is recorded by the owner (a bill line, or by hand) -- everything goes into the stock first; OUT is what the mechanic writes on
paper, and the owner types it once a week as one list (add_moves_batch). Every item has ONE standard name, built from its parts:
    ชนิด + ยี่ห้อ + รุ่น + สเปค     e.g. "แมกเนติก Fuji SC-N8 220V", "โอเวอร์โหลด Mitsubishi TH-T25 18-26A"
(compose_name). The model code is the word a bill is matched by (spaces, dashes and capitals do not matter: SC-N2 = SCN2 = sc n2); where one model comes
in several ratings the word is a pair "TR-N2/3+12-18A" (both must be on the bill line).

Pure rules (clean_item, clean_move, on_hand) are unit-tested; the database calls are thin. Needs schema_v16.sql.
"""
import re
import uuid
from datetime import date, timedelta
from typing import Optional

from app import supabase
from app.purchasing import NEEDS_V16, _machines, has_v16, items_with_aliases, price_stats, today_th

MAX_QTY = 1_000_000
MOVE_KINDS = ("in", "out", "count")


class InvalidStock(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class NotFound(Exception):
    pass


def _text(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def _qty(value, what: str, *, allow_zero: bool) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        n = float(str(value).replace(",", "").strip())
    except ValueError:
        raise InvalidStock(f"{what}ต้องเป็นตัวเลข")
    if n < 0 or (n == 0 and not allow_zero) or n > MAX_QTY:
        raise InvalidStock(f"{what}ไม่ถูกต้อง")
    return n


def compose_name(kind, brand, model, spec) -> str:
    """The standard name: ชนิด + ยี่ห้อ + รุ่น + สเปค, whichever are given, single-spaced."""
    return _text(" ".join(p for p in (_text(kind, 60), _text(brand, 40), _text(model, 60), _text(spec, 200)) if p), 120)


def clean_item(data: dict, *, existing: Optional[dict] = None, others: list[dict]) -> dict:
    """A new item (existing=None) or the fields present in `data`. Names and words are unique across items.
    An item with a ชนิด gets its standard name from the parts (unless a name is typed); changing a part renames it."""
    creating = existing is None
    out: dict = {}
    for key, limit in (("kind", 60), ("brand", 40), ("model", 60), ("category", 40), ("unit", 20), ("spec", 200), ("location", 60), ("notes", 500)):
        if creating or key in data:
            out[key] = _text(data.get(key), limit) or None
    final = {**(existing or {}), **out}
    composed = compose_name(final.get("kind"), final.get("brand"), final.get("model"), final.get("spec")) if final.get("kind") else ""
    typed = _text(data.get("name"), 120) if "name" in data else ""
    parts_changed = any(k in data for k in ("kind", "brand", "model", "spec"))
    if creating or "name" in data or (composed and parts_changed):
        name = typed or composed
        if not name:
            raise InvalidStock("ใส่ชื่อรายการด้วย")
        if any(o["name"].lower() == name.lower() for o in others):
            raise InvalidStock(f"มีรายการชื่อ “{name}” อยู่แล้ว")
        out["name"] = name
    if creating or "min_qty" in data:
        out["min_qty"] = _qty(data.get("min_qty"), "จำนวนขั้นต่ำ", allow_zero=True)
    if "active" in data:
        out["active"] = bool(data["active"])
    if creating and "aliases" not in data and out.get("model"):                 # the model code is what a bill names it by
        if out["model"].lower() not in {a.lower() for o in others for a in o.get("aliases", [])}:
            out["aliases"] = [out["model"]]
    if "aliases" in data or "aliases" in out:
        raw = data.get("aliases", out.get("aliases"))
        if not isinstance(raw, list):
            raise InvalidStock("คำที่ใช้เรียกไม่ถูกต้อง")
        taken = {a.lower(): o["name"] for o in others for a in o.get("aliases", [])}
        words, seen = [], set()
        for w in raw:
            word = _text(w, 120)
            if not word or word.lower() in seen:
                continue
            if word.lower() in taken:
                raise InvalidStock(f"คำว่า “{word}” เป็นของ “{taken[word.lower()]}” อยู่แล้ว")
            seen.add(word.lower())
            words.append(word)
        out["aliases"] = words[:50]
    return out


def clean_move(data: dict, *, today: date, items: set, machines: set, on_hand_now: float = 0) -> dict:
    kind = data.get("kind")
    if kind not in MOVE_KINDS:
        raise InvalidStock("เลือกว่ารับเข้า เบิกออก หรือนับสต็อก")
    try:
        item_id = str(uuid.UUID(str(data.get("item_id"))))
    except ValueError:
        raise InvalidStock("ไม่พบรายการนี้")
    if item_id not in items:
        raise InvalidStock("ไม่พบรายการนี้")
    try:
        d = date.fromisoformat(str(data.get("move_date") or today.isoformat()))
    except ValueError:
        raise InvalidStock("วันที่ไม่ถูกต้อง")
    if d > today + timedelta(days=1) or d.year >= 2400:
        raise InvalidStock("วันที่ไม่ถูกต้อง")
    if kind == "count":
        counted = _qty(data.get("qty"), "จำนวนที่นับได้", allow_zero=True)
        if counted is None:
            raise InvalidStock("ใส่จำนวนที่นับได้")
        qty = round(counted - on_hand_now, 4)
    else:
        n = _qty(data.get("qty"), "จำนวน", allow_zero=False)
        if n is None:
            raise InvalidStock("ใส่จำนวนด้วย")
        qty = n if kind == "in" else -n
    machine_id = data.get("machine_id") or None
    if machine_id and machine_id not in machines:
        raise InvalidStock("ไม่พบเครื่องนี้")
    return {"item_id": item_id, "move_date": d.isoformat(), "kind": kind, "qty": qty, "machine_id": machine_id,
            "notes": _text(data.get("notes"), 300) or None}


def clean_batch(data: dict, *, today: date, items: set, machines: set) -> list[dict]:
    """The weekly list: {kind: out|in, move_date, notes, rows: [{item_id, qty, machine_id, notes}]} -> the moves to write (blank rows skipped).
    All or nothing: the first bad row is named by its number and nothing is written."""
    kind = data.get("kind") or "out"
    if kind not in ("in", "out"):
        raise InvalidStock("เลือกว่ารับเข้าหรือเบิกออก")
    raw = data.get("rows")
    if not isinstance(raw, list):
        raise InvalidStock("ไม่มีรายการให้บันทึก")
    moves = []
    for n, r in enumerate(raw, 1):
        r = r if isinstance(r, dict) else {}
        if not r.get("item_id") and str(r.get("qty") if r.get("qty") is not None else "").strip() == "":
            continue
        try:
            moves.append(clean_move({"kind": kind, "move_date": data.get("move_date"), "item_id": r.get("item_id"), "qty": r.get("qty"), "machine_id": r.get("machine_id"),
                                     "notes": r.get("notes") or data.get("notes")}, today=today, items=items, machines=machines))
        except InvalidStock as exc:
            raise InvalidStock(f"แถวที่ {n}: {exc}")
    if not moves:
        raise InvalidStock("ไม่มีรายการให้บันทึก")
    return moves


def on_hand(moves: list[dict]) -> dict:
    """item id -> what is on the shelf."""
    out: dict = {}
    for m in moves:
        out[m["item_id"]] = round(out.get(m["item_id"], 0) + float(m["qty"]), 4)
    return out


# ------------------------------------------------------------------ database
def _require() -> None:
    if not has_v16():
        raise InvalidStock(NEEDS_V16)


def _moves() -> list[dict]:
    return supabase.select_all("stock_moves", {"select": "item_id,qty", "order": "id.asc"})


def overview() -> dict:
    if not (supabase.configured() and has_v16()):
        return {"ready": False, "items": [], "machines": []}
    items = items_with_aliases()
    have = on_hand(_moves())
    lines = supabase.select_all("purchase_lines", {"select": "item_id,unit_cost,unit_cost_ex_vat,quantity,unit,bill_id,created_at,purchase_bills(bill_date,vendor_name,vendor_id)",
                                                   "item_id": "not.is.null", "order": "created_at.desc"})
    from app import shops
    names = {s["id"]: s["name"] for s in shops._load()}
    by: dict = {}
    for ln in lines:
        b = ln.pop("purchase_bills", None) or {}
        by.setdefault(ln["item_id"], []).append({**ln, "bill_date": b.get("bill_date"), "vendor": names.get(b.get("vendor_id")) or b.get("vendor_name")})
    out = []
    for it in items:
        stats = price_stats(by.get(it["id"], []))
        qty = have.get(it["id"], 0)
        low = it.get("min_qty") is not None and qty < float(it["min_qty"])
        out.append({**it, "on_hand": qty, "low": low, "last": stats["last"], "lowest": stats["lowest"]})
    return {"ready": True, "items": out, "machines": _machines()}


def create_item(data: dict) -> dict:
    _require()
    items = items_with_aliases()
    values = clean_item(data, others=items)
    aliases = values.pop("aliases", [])
    row = supabase.insert("stock_items", [values])[0]
    if aliases:
        supabase.insert("stock_item_aliases", [{"item_id": row["id"], "alias": a} for a in aliases])
    return overview()


def create_items_batch(data: dict) -> dict:
    """Several new items at once: {category, rows: [{kind, brand, model, spec, unit, qty}]}. Blank rows are skipped; every row is checked first (names and
    bill words must be unique across the list too) and the first bad one is named by its number, with nothing written. `qty` is the count to start from."""
    _require()
    raw = data.get("rows")
    if not isinstance(raw, list):
        raise InvalidStock("ไม่มีรายการให้บันทึก")
    category = _text(data.get("category"), 40)
    others = items_with_aliases()
    cleaned = []
    for n, r in enumerate(raw, 1):
        r = r if isinstance(r, dict) else {}
        if not any(_text(r.get(k), 200) for k in ("kind", "brand", "model", "spec", "name")):
            continue
        try:
            v = clean_item({**r, **({"category": category} if category and not _text(r.get("category"), 40) else {})}, others=others)
            qty = _qty(r.get("qty"), "จำนวน", allow_zero=True)
        except InvalidStock as exc:
            raise InvalidStock(f"แถวที่ {n}: {exc}")
        aliases = v.pop("aliases", [])
        others = others + [{**v, "aliases": aliases}]
        cleaned.append((v, aliases, qty))
    if not cleaned:
        raise InvalidStock("ไม่มีรายการให้บันทึก")
    today = today_th().isoformat()
    for v, aliases, qty in cleaned:
        row = supabase.insert("stock_items", [v])[0]
        if aliases:
            supabase.insert("stock_item_aliases", [{"item_id": row["id"], "alias": a} for a in aliases])
        if qty:
            supabase.insert("stock_moves", [{"item_id": row["id"], "move_date": today, "kind": "count", "qty": qty, "notes": "ยอดเริ่มต้น"}])
    out = overview()
    out["created"] = len(cleaned)
    return out


def update_item(item_id: str, data: dict) -> dict:
    _require()
    items = items_with_aliases()
    existing = next((i for i in items if i["id"] == item_id), None)
    if existing is None:
        raise NotFound()
    values = clean_item(data, existing=existing, others=[i for i in items if i["id"] != item_id])
    aliases = values.pop("aliases", None)
    if values:
        supabase.update("stock_items", {"id": f"eq.{item_id}"}, values)
    if aliases is not None:
        supabase.delete("stock_item_aliases", {"item_id": f"eq.{item_id}"})
        if aliases:
            supabase.insert("stock_item_aliases", [{"item_id": item_id, "alias": a} for a in aliases])
    return overview()


def add_move(data: dict) -> dict:
    _require()
    items = {i["id"] for i in supabase.select_all("stock_items", {"select": "id", "order": "id.asc"})}
    item_id = str(data.get("item_id") or "")
    now = on_hand([m for m in _moves() if m["item_id"] == item_id]).get(item_id, 0)
    row = clean_move(data, today=today_th(), items=items, machines={m["id"] for m in _machines()}, on_hand_now=now)
    if row["kind"] == "count" and row["qty"] == 0:
        return overview()                                     # counted what the system already had: nothing to write
    supabase.insert("stock_moves", [row])
    return overview()


def add_moves_batch(data: dict) -> dict:
    """Write the weekly out-list (or an in-list) in one go. `negative` names the items that now show less than nothing on the shelf, so a missing
    opening count or a wrong quantity is noticed -- the moves are still written (the paper is the truth, the records catch up)."""
    _require()
    items = {i["id"]: i for i in items_with_aliases()}
    moves = clean_batch(data, today=today_th(), items=set(items), machines={m["id"] for m in _machines()})
    supabase.insert("stock_moves", moves)
    have = on_hand(_moves())
    out = overview()
    out["negative"] = sorted(items[i]["name"] for i in {m["item_id"] for m in moves} if have.get(i, 0) < 0)
    out["written"] = len(moves)
    return out


def item_moves(item_id: str) -> list[dict]:
    _require()
    try:
        uuid.UUID(item_id)
    except ValueError:
        raise NotFound()
    rows = supabase.select_all("stock_moves", {"select": "*", "item_id": f"eq.{item_id}", "order": "move_date.desc,created_at.desc"})
    machines = {m["id"]: m["name"] for m in _machines()}
    return [{**r, "machine_name": machines.get(r.get("machine_id"))} for r in rows]


def delete_move(move_id: str) -> None:
    _require()
    try:
        uuid.UUID(move_id)
    except ValueError:
        raise NotFound()
    supabase.delete("stock_moves", {"id": f"eq.{move_id}"})
