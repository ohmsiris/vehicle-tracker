"""
Tyre jobs and the spare-tyre store (ยางสำรอง). Needs schema_v40.sql.

A TYRE JOB (งานยาง) is what really happens to a truck in one visit: for each wheel, keep its tyre, move another wheel's tyre onto it
("the two fronts go to the rear inner wheels"), put a NEW tyre on, or put a SPARE from the store on. A tyre that ends up on no wheel
must be accounted for -- why it came off (REASONS) and where it went (into the store as a spare, or scrapped) -- so nothing is ever
silently overwritten. Sizes stay on their own axle (tyres.wrong_axle).

The STORE is counted by size, not numbered: tyre_stock_moves is a ledger (+1 a tyre off a truck into the store / a purchase,
-1 a spare onto a truck / one thrown away, +/- a correction after a real count). On hand = the sum per size; each size can have a
minimum (below it = buy more). The monthly real count catches spares that went out without being recorded ("หายไป 1 เส้น").
ช่างเอ๋ decides whether a broken tyre is fixed (back into the store) or thrown away; the owner / the bookkeeper records it.

clean_job / count_diffs are pure (unit-tested); the rest is thin database code.
"""
import re
import time
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from app import supabase
from app.tyres import (LABELS, UNKNOWN, InvalidTyre, NotFound, _date, _int, _money, _rows, _spec, _text, _vehicle, default_wheels, norm_size,
                       positions, vehicle_tyres, wrong_axle)

REASONS = {"worn": "ดอกหมด", "burst": "แตก / บวม", "puncture": "ตะปู / ฉีก", "age": "ครบอายุ", "other": "อื่นๆ"}
DESTINATIONS = {"spare": "เก็บเป็นยางสำรอง", "scrap": "ทิ้ง"}
ACTIONS = ("keep", "move", "new", "spare")
KIND_TEXT = {"from_truck": "ถอดจากรถเข้าสต็อก", "purchase": "ซื้อเข้า", "to_truck": "ใส่รถ", "scrap": "ทิ้ง", "count": "ปรับตามนับจริง"}
NEEDS_V40 = "ต้องรัน schema_v40.sql ใน Supabase ก่อน จึงจะใช้ยางสำรองได้"
_probe = {"at": 0.0, "ok": False}


def has_v40() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("tyre_stock_moves", {"select": "id", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not has_v40():
        raise InvalidTyre(NEEDS_V40)


# ------------------------------------------------------------------ pure
_BIAS = re.compile(r"(?<![\d.])(\d{1,2}\.\d{2})\s*[-R]\s*(\d{2}(?:\.\d)?)", re.I)


def store_size(text) -> str:
    """The store's key for a size: radial "225/75R14", or a big-truck "7.50-16" (written 7.50-16 / 7.50R16 / 7.50 - 16)."""
    s = norm_size(text or "")
    if s:
        return s
    m = _BIAS.search(str(text or ""))
    return f"{m.group(1)}-{m.group(2)}" if m else ""


def clean_removal(raw, size: Optional[str], label: str) -> dict:
    """Why a tyre came off and where it went -- both are required."""
    raw = raw if isinstance(raw, dict) else {}
    reason, to = raw.get("reason"), raw.get("to")
    if reason not in REASONS:
        raise InvalidTyre(f"ยาง{label}ที่ถอดออก: เลือกว่าเปลี่ยนเพราะอะไร")
    if to not in DESTINATIONS:
        raise InvalidTyre(f"ยาง{label}ที่ถอดออก: เลือกว่าเก็บเป็นยางสำรองหรือทิ้ง")
    if to == "spare" and not store_size(size):
        raise InvalidTyre(f"ยาง{label}: ไม่รู้ขนาด จึงเก็บเข้าสต็อกไม่ได้ — แก้ขนาดยางเส้นนี้ก่อน หรือเลือก “ทิ้ง”")
    return {"reason": reason, "to": to}


def clean_job(data: dict, current: dict, wheels: int, spec: Optional[dict], today: date) -> dict:
    """One tyre job. `current` = position -> the tyre on that wheel now (rows of `tyres`). Returns
    {done_on, km, moves: [(tyre, dst)], removed: [(tyre, {reason, to})], added: [{position, source, size, brand, price, vendor, event_id}]}."""
    allowed = positions(wheels)
    done_on = _date(data.get("done_on"), "วันที่ทำ", today)
    if not done_on:
        raise InvalidTyre("ใส่วันที่ทำงานยาง")
    km = _int(data.get("km"), "เลขไมล์")
    vendor = _text(data.get("vendor"), 100)
    plan = data.get("wheels") if isinstance(data.get("wheels"), dict) else {}
    for p in plan:
        if p not in allowed:
            raise InvalidTyre(f"ตำแหน่งล้อไม่ถูกต้อง: {p}")
    final: dict = {}                                   # position -> ("old", tyre) | ("new", spec)
    used_sources: set = set()
    added = []
    for p in allowed:
        w = plan.get(p) if isinstance(plan.get(p), dict) else {"action": "keep"}
        action = w.get("action") or "keep"
        if action not in ACTIONS:
            raise InvalidTyre(f"ล้อ{LABELS[p]}: ไม่รู้จักงานนี้")
        if action == "keep":
            if p in current:
                final[p] = ("old", current[p])
        elif action == "move":
            src = w.get("from")
            if src not in current or src == p:
                raise InvalidTyre(f"ล้อ{LABELS[p]}: เลือกล้อที่ย้ายยางมา")
            if src in used_sources:
                raise InvalidTyre(f"ยางล้อ{LABELS[src]}ถูกย้ายไปสองที่")
            used_sources.add(src)
            why = wrong_axle(current[src].get("size"), p, spec)
            if why:
                raise InvalidTyre(f"ย้ายยาง{LABELS[src]}ไป{LABELS[p]}: {why}")
            final[p] = ("old", current[src])
        else:
            size_raw = _text(w.get("size"), 40) or ((spec or {}).get("front_size") if p in ("FL", "FR") else (spec or {}).get("rear_size"))
            size = (store_size(size_raw) or size_raw) if size_raw else None
            if action == "spare" and not store_size(size):
                raise InvalidTyre(f"ล้อ{LABELS[p]}: ใส่ขนาดของยางสำรองที่ใช้")
            why = wrong_axle(size, p, spec)
            if why:
                raise InvalidTyre(f"ล้อ{LABELS[p]}: {why}")
            event_id = w.get("event_id") or None
            if event_id:
                try:
                    event_id = str(uuid.UUID(str(event_id)))
                except ValueError:
                    raise InvalidTyre("บิลที่เลือกไม่ถูกต้อง")
            new = {"position": p, "source": "spare" if action == "spare" else "new", "size": size, "brand": _text(w.get("brand"), 60),
                   "price": _money(w.get("price")) if action == "new" else None, "vendor": vendor if action == "new" else None, "event_id": event_id}
            added.append(new)
            final[p] = ("new", new)
    # a tyre that was kept in place but whose wheel was given to another tyre is a conflict, caught here: each old tyre at most once
    seen = [t["id"] for kind, t in final.values() if kind == "old"]
    if len(seen) != len(set(seen)):
        raise InvalidTyre("ยางเส้นเดียวอยู่สองล้อ — ล้อที่ “คงเดิม” ต้องไม่ถูกย้ายยางออกไปด้วย")
    on_wheels = set(seen)
    moves = [(t, p) for p, (kind, t) in final.items() if kind == "old" and t["position"] != p]
    removals_raw = data.get("removed") if isinstance(data.get("removed"), dict) else {}
    removed = []
    for t in current.values():
        if t["id"] not in on_wheels:
            removed.append((t, clean_removal(removals_raw.get(t["id"]), t.get("size"), LABELS.get(t["position"], ""))))
    if not moves and not removed and not added:
        raise InvalidTyre("ยังไม่ได้เปลี่ยนอะไรเลย")
    return {"done_on": done_on, "km": km, "moves": moves, "removed": removed, "added": added, "notes": _text(data.get("notes"), 300)}


def count_diffs(counted: dict, on_hand: dict) -> list[dict]:
    """A real count against what the ledger says: the sizes that differ (+ found more, - missing)."""
    out = []
    for size, n in counted.items():
        have = on_hand.get(size, 0)
        if n != have:
            out.append({"size": size, "counted": n, "expected": have, "diff": n - have})
    return out


# ------------------------------------------------------------------ database
def _move(size: str, qty: int, kind: str, moved_on: str, *, vehicle_id=None, tyre_id=None, brand=None, notes=None) -> None:
    supabase.insert("tyre_stock_moves", [{"size": size, "qty": qty, "kind": kind, "moved_on": moved_on, "vehicle_id": vehicle_id, "tyre_id": tyre_id,
                                          "brand": brand, "notes": notes}])


def remove_into(t: dict, removal: dict, removed_on: str, km: Optional[int], vehicle_id: str) -> None:
    """Take one tyre off its wheel: why, where to; a usable one goes into the store (+1 of its size)."""
    supabase.update("tyres", {"id": f"eq.{t['id']}"}, {"removed_on": removed_on, "removed_km": km, "removed_reason": removal["reason"], "removed_to": removal["to"]})
    if removal["to"] == "spare":
        _move(store_size(t.get("size")), 1, "from_truck", removed_on, vehicle_id=vehicle_id, tyre_id=t["id"], brand=t.get("brand"),
              notes=f"ถอดจาก{LABELS.get(t['position'], '')} · {REASONS[removal['reason']]}")


def tyre_job(vehicle_id: str, data: dict) -> dict:
    from app.service_plan import today_th
    _require()
    v = _vehicle(vehicle_id)
    spec = _spec(v["id"])
    wheels = (spec or {}).get("wheels") or default_wheels(v)
    current = {r["position"]: r for r in _rows(v["id"]) if not r.get("removed_on") and r["position"] != UNKNOWN}
    job = clean_job(data, current, wheels, spec, today_th())
    for t, removal in job["removed"]:
        remove_into(t, removal, job["done_on"], job["km"], v["id"])
    for t, dst in job["moves"]:
        note = f"ย้ายจาก{LABELS[t['position']]} ({job['done_on']})"
        supabase.update("tyres", {"id": f"eq.{t['id']}"}, {"position": dst, "notes": f"{t['notes']} · {note}" if t.get("notes") else note})
    for a in job["added"]:
        row = supabase.insert("tyres", [{"vehicle_id": v["id"], "position": a["position"], "size": a["size"], "brand": a["brand"], "fitted_on": job["done_on"],
                                         "fitted_km": job["km"], "price": a["price"], "vendor": a["vendor"], "event_id": a["event_id"], "source": a["source"],
                                         "notes": job["notes"]}])[0]
        if a["source"] == "spare":
            _move(store_size(a["size"]), -1, "to_truck", job["done_on"], vehicle_id=v["id"], tyre_id=row["id"], brand=a["brand"], notes=f"ใส่{LABELS[a['position']]}")
    return vehicle_tyres(v["id"])


def _on_hand() -> dict:
    out: dict = {}
    for m in supabase.select_all("tyre_stock_moves", {"select": "size,qty", "order": "id.asc"}):
        out[m["size"]] = out.get(m["size"], 0) + int(m["qty"])
    return out


def stock_view() -> dict:
    if not has_v40():
        return {"ready": False, "sizes": [], "moves": []}
    on_hand = _on_hand()
    size_rows = supabase.select_all("tyre_stock_sizes", {"select": "size,min_qty,counted_on", "order": "size.asc"})
    mins = {r["size"]: r["min_qty"] for r in size_rows}
    specs = supabase.select_all("vehicle_tyre_specs", {"select": "front_size,rear_size", "order": "vehicle_id.asc"})
    in_use: dict = {}
    for s in specs:
        for k in ("front_size", "rear_size"):
            if s.get(k):
                in_use[s[k]] = in_use.get(s[k], 0) + 1
    sizes = sorted(set(on_hand) | set(mins) | set(in_use))
    moves = supabase.select("tyre_stock_moves", {"select": "*", "order": "moved_on.desc,created_at.desc", "limit": "60"})
    vids = {m["vehicle_id"] for m in moves if m.get("vehicle_id")}
    trucks = {v["id"]: v for v in supabase.select_all("vehicles", {"select": "id,vehicle_number,plate_number", "id": f"in.({','.join(vids)})", "order": "id.asc"})} if vids else {}
    counted_days = [str(r["counted_on"]) for r in size_rows if r.get("counted_on")]
    return {"ready": True, "reasons": REASONS, "kinds": KIND_TEXT,
            "sizes": [{"size": s, "on_hand": on_hand.get(s, 0), "min": mins.get(s, 0), "trucks": in_use.get(s, 0), "low": on_hand.get(s, 0) < mins.get(s, 0)} for s in sizes],
            "moves": [{**m, "truck": (lambda t: " · ".join(x for x in (f"#{t.get('vehicle_number')}" if t.get("vehicle_number") else "", t.get("plate_number") or "") if x))(trucks.get(m.get("vehicle_id"), {}))}
                      for m in moves],
            "last_count": max(counted_days) if counted_days else None}


def _clean_size(value) -> str:
    s = store_size(value)
    if not s:
        raise InvalidTyre("ขนาดยางไม่ถูกต้อง เช่น 225/75R14 หรือ 7.50-16")
    return s


def add_moves(data: dict) -> dict:
    """ซื้อเข้า (+n) or ทิ้ง (-n) straight from the store."""
    from app.service_plan import today_th
    _require()
    kind = data.get("kind")
    if kind not in ("purchase", "scrap"):
        raise InvalidTyre("เลือกว่าเป็นการซื้อเข้าหรือทิ้ง")
    size = _clean_size(data.get("size"))
    qty = _int(data.get("qty"), "จำนวน") or 0
    if not 1 <= qty <= 100:
        raise InvalidTyre("จำนวนต้อง 1–100 เส้น")
    moved_on = _date(data.get("moved_on"), "วันที่", today_th()) or today_th().isoformat()
    _move(size, qty if kind == "purchase" else -qty, kind, moved_on, brand=_text(data.get("brand"), 60), notes=_text(data.get("notes"), 200))
    return stock_view()


def count_stock(data: dict) -> dict:
    """The real count: {counts: {size: n}}. Each size that differs gets a correction in the ledger; the differences are returned."""
    from app.service_plan import today_th
    _require()
    raw = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    counted = {}
    for size, n in raw.items():
        if n is None or str(n).strip() == "":
            continue
        counted[_clean_size(size)] = _int(n, f"จำนวน {size}") or 0
    if not counted:
        raise InvalidTyre("ใส่จำนวนที่นับได้อย่างน้อยหนึ่งขนาด")
    day = today_th().isoformat()
    diffs = count_diffs(counted, _on_hand())
    for d in diffs:
        _move(d["size"], d["diff"], "count", day, notes=f"นับจริง {d['counted']} เส้น (ระบบ {d['expected']})")
    now = datetime.now(timezone.utc).isoformat()
    supabase.insert("tyre_stock_sizes", [{"size": s, "min_qty": _min_of(s), "counted_on": day, "updated_at": now} for s in counted], on_conflict="size")
    return {**stock_view(), "diffs": diffs, "counted_on": day}


def _min_of(size: str) -> int:
    rows = supabase.select("tyre_stock_sizes", {"select": "min_qty", "size": f"eq.{size}", "limit": "1"})
    return rows[0]["min_qty"] if rows else 0


def set_min(data: dict) -> dict:
    _require()
    size = _clean_size(data.get("size"))
    n = _int(data.get("min"), "ขั้นต่ำ") or 0
    if n > 50:
        raise InvalidTyre("ขั้นต่ำมากเกินไป")
    rows = supabase.select("tyre_stock_sizes", {"select": "counted_on", "size": f"eq.{size}", "limit": "1"})
    supabase.insert("tyre_stock_sizes", [{"size": size, "min_qty": n, "counted_on": rows[0]["counted_on"] if rows else None,
                                          "updated_at": datetime.now(timezone.utc).isoformat()}], on_conflict="size")
    return stock_view()


def low_sizes() -> list[dict]:
    """The sizes below their minimum (for reminders)."""
    try:
        return [s for s in stock_view()["sizes"] if s["low"]]
    except supabase.SupabaseError:
        return []
