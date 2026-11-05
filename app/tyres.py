"""
Tyres on the truck page: a chassis drawing with the tyre on each wheel (brand, size, since when, at what km) and the wheel's history.
Needs schema_v36.sql (vehicle_tyre_specs, tyres).

A truck has 4 wheels (FL FR RL RR) or 6 (dual rear: FL FR RLO RLI RRI RRO, the front wheels in line with the OUTER rear ones). A tyre bill
already saved on the truck shows up as "not placed yet" until its tyres are put on wheels; a tyre whose wheel isn't known (an old bill)
can sit at position '?'. Changing a wheel's tyre closes the old one (removed_on) and adds the new one, so nothing is overwritten.
"""
import re
import time
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from app import supabase

FOUR = ("FL", "FR", "RL", "RR")
SIX = ("FL", "FR", "RLO", "RLI", "RRI", "RRO")
UNKNOWN = "?"
LABELS = {"FL": "หน้าซ้าย", "FR": "หน้าขวา", "RL": "หลังซ้าย", "RR": "หลังขวา", "RLO": "หลังซ้ายนอก", "RLI": "หลังซ้ายใน",
          "RRI": "หลังขวาใน", "RRO": "หลังขวานอก", UNKNOWN: "ไม่ทราบตำแหน่ง"}
NEEDS_V36 = "ต้องรัน schema_v36.sql ใน Supabase ก่อน จึงจะบันทึกยางได้"
SIZE_RE = re.compile(r"\b(\d{3})\s*/\s*(\d{2})\s*(?:Z?R|-)?\s*(\d{2}(?:\.\d)?)\b", re.I)
RADIAL_SIZE = re.compile(r"(?<!\d)\d{3}\s*/\s*\d{2}\s*Z?R\s*\d{2}", re.I)
TYRE_LINE = re.compile(r"ยาง(?:เรเดียล|เรเดีย|นอก|ใหม่|รถ)?|tire|tyre", re.I)
NOT_A_TYRE = re.compile(r"ยางใน|จุ๊บ|ปะ|ยางกันฝุ่น|ยางแท่น|ยางรอง|ยางหูโช้ค|ยางกันกระแทก|ยางปัด|ท่อยาง|ลูกยาง|ยางฝา|ยางเบ้า|ยางกัน|ยางโอริง|ยางหิ้ว|ยางรัด|ซีลยาง")
BRANDS = ("MICHELIN", "BRIDGESTONE", "DUNLOP", "YOKOHAMA", "GOODYEAR", "MAXXIS", "DEESTONE", "OTANI", "TOYO", "HANKOOK", "FALKEN", "CONTINENTAL", "KUMHO", "NITTO", "PIRELLI", "LENSO")
_probe = {"at": 0.0, "ok": False}


class InvalidTyre(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class NotFound(Exception):
    pass


def has_v36() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("tyres", {"select": "id", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


# ------------------------------------------------------------------ pure
def positions(wheels: int) -> tuple:
    return SIX if wheels == 6 else FOUR


def default_wheels(vehicle: dict) -> int:
    return 6 if (vehicle.get("vehicle_class") or "") == "รถใหญ่" else 4


def norm_size(text) -> str:
    """'225/75 R14', '225/75-14', '225 / 75R14' -> '225/75R14' (empty when there is no size in it)."""
    m = SIZE_RE.search(str(text or ""))
    return f"{m.group(1)}/{m.group(2)}R{m.group(3)}" if m else ""


def is_tyre_line(description) -> bool:
    """A bill line that is a TYRE (not a tube, a valve, a puncture repair or a rubber part that happens to say ยาง)."""
    text = str(description or "")
    if NOT_A_TYRE.search(text):
        return False
    if RADIAL_SIZE.search(text):                      # "225/75R14": only ever a tyre -- even when the reading got the word wrong ("ขางเรเดียล")
        return True
    return bool((TYRE_LINE.search(text) or "เรเดียล" in text) and (SIZE_RE.search(text) or re.search(r"เรเดียล|ยางนอก", text)))


def brand_of(description) -> str:
    up = str(description or "").upper()
    return next((b.title() for b in BRANDS if b in up), "")


def _text(value, limit: int) -> Optional[str]:
    t = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]
    return t or None


def _int(value, what: str) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        n = int(float(str(value).replace(",", "").strip()))
    except ValueError:
        raise InvalidTyre(f"{what}ต้องเป็นตัวเลข")
    if n < 0 or n > 5_000_000:
        raise InvalidTyre(f"{what}ไม่ถูกต้อง")
    return n


def _money(value) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        n = float(str(value).replace(",", "").strip())
    except ValueError:
        raise InvalidTyre("ราคาต้องเป็นตัวเลข")
    if n < 0 or n > 1_000_000:
        raise InvalidTyre("ราคาไม่ถูกต้อง")
    return round(n, 2)


def _date(value, what: str, today: date) -> Optional[str]:
    if not value:
        return None
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        raise InvalidTyre(f"{what}ไม่ถูกต้อง")
    if d.year >= 2400:
        raise InvalidTyre(f"{what}: ใส่ปี ค.ศ. (ไม่ใช่ พ.ศ.)")
    if d > today:
        raise InvalidTyre(f"{what}เป็นวันในอนาคต")
    return d.isoformat()


def clean_spec(data: dict) -> dict:
    out: dict = {}
    for key in ("front_size", "rear_size"):
        if key in data:
            raw = _text(data.get(key), 40)
            out[key] = (norm_size(raw) or raw) if raw else None
    if "wheels" in data:
        w = _int(data.get("wheels"), "จำนวนล้อ")
        if w not in (4, 6):
            raise InvalidTyre("จำนวนล้อต้องเป็น 4 หรือ 6")
        out["wheels"] = w
    for key in ("radial", "tubeless"):
        if key in data:
            out[key] = None if data[key] is None else bool(data[key])
    if "notes" in data:
        out["notes"] = _text(data.get("notes"), 300)
    return out


FRONT = ("FL", "FR")


def wrong_axle(size: Optional[str], position: str, spec: Optional[dict]) -> Optional[str]:
    """Why `size` cannot go on `position`, or None. Only when the truck's front and rear sizes are recorded and differ: the rear size
    never goes on a front wheel and the other way round (225/75R14 at the front of a D-Max is a mistake, not a choice)."""
    s = norm_size(size or "")
    front, rear = norm_size((spec or {}).get("front_size") or ""), norm_size((spec or {}).get("rear_size") or "")
    if not s or not front or not rear or front == rear or position == UNKNOWN:
        return None
    if position in FRONT and s == rear:
        return f"{s} เป็นขนาดยางหลังของรถคันนี้ — ใส่ล้อหน้าไม่ได้"
    if position not in FRONT and s == front:
        return f"{s} เป็นขนาดยางหน้าของรถคันนี้ — ใส่ล้อหลังไม่ได้"
    return None


def clean_change(data: dict, wheels: int, today: date, spec: Optional[dict] = None) -> tuple[list[str], dict]:
    """"บันทึกเปลี่ยนยาง": the wheels changed (+ how many tyres whose wheel isn't known) and the new tyres' details."""
    allowed = positions(wheels)
    picked = [p for p in (data.get("positions") or []) if p]
    bad = [p for p in picked if p not in allowed]
    if bad:
        raise InvalidTyre(f"ตำแหน่งล้อไม่ถูกต้อง: {', '.join(bad)}")
    if len(set(picked)) != len(picked):
        raise InvalidTyre("เลือกล้อซ้ำ")
    unknown = _int(data.get("unknown_count"), "จำนวนเส้นที่ไม่ทราบตำแหน่ง") or 0
    if unknown > 12:
        raise InvalidTyre("จำนวนเส้นที่ไม่ทราบตำแหน่งมากเกินไป")
    if not picked and not unknown:
        raise InvalidTyre("เลือกล้อที่เปลี่ยนอย่างน้อย 1 ล้อ")
    fitted_on = _date(data.get("fitted_on"), "วันที่เปลี่ยน", today)
    if not fitted_on:
        raise InvalidTyre("ใส่วันที่เปลี่ยนยาง")
    event_id = data.get("event_id") or None
    if event_id:
        try:
            event_id = str(uuid.UUID(str(event_id)))
        except ValueError:
            raise InvalidTyre("บิลที่เลือกไม่ถูกต้อง")
    size = _text(data.get("size"), 40)
    tyre = {"brand": _text(data.get("brand"), 60), "size": (norm_size(size) or size) if size else None, "fitted_on": fitted_on,
            "fitted_km": _int(data.get("fitted_km"), "เลขไมล์"), "price": _money(data.get("price")), "vendor": _text(data.get("vendor"), 100),
            "event_id": event_id, "notes": _text(data.get("notes"), 300)}
    for p in picked:
        why = wrong_axle(tyre["size"], p, spec)
        if why:
            raise InvalidTyre(f"{why} (ถ้าขนาดยางที่บันทึกไว้ของรถผิด แก้ได้ที่ “ขนาดยาง”)")
    return picked + [UNKNOWN] * unknown, tyre


def clean_rotation(moves: dict, current: dict, wheels: int, spec: Optional[dict] = None) -> dict:
    """"สลับยาง": {from position: to position} for the tyres on the truck now. Every target is a real wheel and no two tyres land on one."""
    allowed = positions(wheels)
    if not isinstance(moves, dict) or not moves:
        raise InvalidTyre("ยังไม่ได้เลือกว่าจะสลับยางไปล้อไหน")
    out = {}
    for src, dst in moves.items():
        if src not in current:
            raise InvalidTyre(f"ล้อ{LABELS.get(src, src)}ไม่มียางที่บันทึกไว้")
        if dst not in allowed:
            raise InvalidTyre(f"ตำแหน่งล้อไม่ถูกต้อง: {dst}")
        why = wrong_axle(current[src].get("size"), dst, spec) if src != dst else None
        if why:
            raise InvalidTyre(f"ยาง{LABELS.get(src, src)}: {why}")
        if src != dst:
            out[src] = dst
    if not out:
        raise InvalidTyre("ยังไม่ได้สลับล้อไหนเลย")
    final = {p: p for p in current}
    for src, dst in out.items():
        final[src] = dst
    targets = list(final.values())
    if len(set(targets)) != len(targets):
        raise InvalidTyre("ยาง 2 เส้นไปอยู่ล้อเดียวกัน — ล้อที่ถูกแทนต้องย้ายไปล้ออื่นด้วย")
    return out


def wheel_view(spec: Optional[dict], vehicle: dict, rows: list[dict], today: date, km_now: Optional[int]) -> dict:
    """What the page draws: the spec, the tyre on each wheel now (with age and km used), the wheels' history, the tyres of unknown wheel."""
    wheels = (spec or {}).get("wheels") or default_wheels(vehicle)
    current: dict = {}
    for r in sorted(rows, key=lambda r: (str(r.get("fitted_on") or ""), str(r.get("created_at") or ""))):
        if not r.get("removed_on") and r["position"] != UNKNOWN:
            current[r["position"]] = r
    out_wheels = []
    for p in positions(wheels):
        t = current.get(p)
        months = km_used = None
        if t and t.get("fitted_on"):
            d = date.fromisoformat(str(t["fitted_on"])[:10])
            months = max(0, (today.year - d.year) * 12 + today.month - d.month - (1 if today.day < d.day else 0))
        if t and t.get("fitted_km") is not None and km_now is not None and km_now >= t["fitted_km"]:
            km_used = km_now - t["fitted_km"]
        history = sorted([r for r in rows if r["position"] == p and r.get("removed_on")], key=lambda r: str(r.get("fitted_on") or ""), reverse=True)
        out_wheels.append({"position": p, "label": LABELS[p], "axle": "front" if p in ("FL", "FR") else "rear", "tyre": t, "months": months, "km_used": km_used,
                           "state": "none" if not t else "unknown" if months is None else "ok" if months < 12 else "warn" if months < 24 else "old",
                           "history": history})
    loose = [r for r in rows if r["position"] == UNKNOWN and not r.get("removed_on")]
    return {"wheels": wheels, "spec": {**(spec or {}), "wheels": wheels, "saved": bool(spec)}, "positions": out_wheels, "unknown": loose, "km_now": km_now,
            "labels": LABELS}


def bill_tyres(events: list[dict], placed_events: set) -> list[dict]:
    """The tyre lines on this truck's bills that no tyre on a wheel points to yet: "ยังไม่ได้ใส่ตำแหน่ง", one entry per line."""
    out = []
    for ev in events:
        if ev.get("id") in placed_events:
            continue
        for ln in ev.get("bill_line_items") or []:
            if not is_tyre_line(ln.get("description")):
                continue
            qty = ln.get("quantity")
            out.append({"event_id": ev["id"], "date": ev.get("event_date"), "mileage": ev.get("mileage"), "vendor": ev.get("vendor"),
                        "description": ln.get("description"), "qty": int(qty) if qty and float(qty).is_integer() else qty,
                        "unit_cost": ln.get("unit_cost") or (ln.get("line_total") / qty if qty and ln.get("line_total") else None),
                        "size": norm_size(ln.get("description")), "brand": brand_of(ln.get("description"))})
    return sorted(out, key=lambda x: str(x["date"] or ""), reverse=True)


# ------------------------------------------------------------------ database
def _require() -> None:
    if not has_v36():
        raise InvalidTyre(NEEDS_V36)


def _vehicle(vehicle_id: str) -> dict:
    try:
        vid = str(uuid.UUID(str(vehicle_id)))
    except ValueError:
        raise NotFound()
    rows = supabase.select("vehicles", {"select": "id,vehicle_class,make,model,nickname", "id": f"eq.{vid}", "limit": "1"})
    if not rows:
        raise NotFound()
    return rows[0]


def _rows(vehicle_id: str) -> list[dict]:
    return supabase.select_all("tyres", {"select": "*", "vehicle_id": f"eq.{vehicle_id}", "order": "fitted_on.asc.nullsfirst,id.asc"})


def _spec(vehicle_id: str) -> Optional[dict]:
    rows = supabase.select("vehicle_tyre_specs", {"select": "*", "vehicle_id": f"eq.{vehicle_id}", "limit": "1"})
    return rows[0] if rows else None


def _km_now(vehicle_id: str) -> Optional[int]:
    from app import history
    rows = [r for r in history.list_vehicles(include_inactive=True) if r["vehicle_id"] == vehicle_id]
    return int(rows[0]["last_known_mileage"]) if rows and rows[0].get("last_known_mileage") else None


def vehicle_tyres(vehicle_id: str, *, today: Optional[date] = None) -> dict:
    from app.service_plan import today_th
    today = today or today_th()
    v = _vehicle(vehicle_id)
    if not has_v36():
        return {"ready": False, **wheel_view(None, v, [], today, None), "bills": []}
    rows = _rows(v["id"])
    events = supabase.select_all("maintenance_events", {"select": "id,event_date,mileage,vendor,bill_line_items(description,quantity,unit_cost,line_total)",
                                                        "vehicle_id": f"eq.{v['id']}", "status": "eq.confirmed", "order": "event_date.desc"})
    placed = {r["event_id"] for r in rows if r.get("event_id")}
    return {"ready": True, **wheel_view(_spec(v["id"]), v, rows, today, _km_now(v["id"])), "bills": bill_tyres(events, placed)}


def save_spec(vehicle_id: str, data: dict) -> dict:
    _require()
    v = _vehicle(vehicle_id)
    values = clean_spec(data)
    if values:
        supabase.insert("vehicle_tyre_specs", [{"vehicle_id": v["id"], **values, "updated_at": datetime.now(timezone.utc).isoformat()}], on_conflict="vehicle_id")
    return vehicle_tyres(v["id"])


def record_change(vehicle_id: str, data: dict) -> dict:
    from app.service_plan import today_th
    _require()
    v = _vehicle(vehicle_id)
    spec = _spec(v["id"])
    wheels = (spec or {}).get("wheels") or default_wheels(v)
    picked, tyre = clean_change(data, wheels, today_th(), spec)
    rows = _rows(v["id"])
    for p in {p for p in picked if p != UNKNOWN}:
        for old in [r for r in rows if r["position"] == p and not r.get("removed_on")]:
            supabase.update("tyres", {"id": f"eq.{old['id']}"}, {"removed_on": tyre["fitted_on"], "removed_km": tyre["fitted_km"]})
    size_of = lambda p: tyre["size"] or ((spec or {}).get("front_size") if p in ("FL", "FR") else (spec or {}).get("rear_size"))   # noqa: E731
    supabase.insert("tyres", [{"vehicle_id": v["id"], "position": p, **tyre, "size": size_of(p)} for p in picked])
    return vehicle_tyres(v["id"])


def rotate(vehicle_id: str, data: dict) -> dict:
    _require()
    v = _vehicle(vehicle_id)
    spec = _spec(v["id"])
    wheels = (spec or {}).get("wheels") or default_wheels(v)
    current = {r["position"]: r for r in _rows(v["id"]) if not r.get("removed_on") and r["position"] != UNKNOWN}
    moves = clean_rotation(data.get("moves") or {}, current, wheels, spec)
    for src, dst in moves.items():
        note = f"สลับจาก{LABELS[src]}"
        old = current[src].get("notes")
        supabase.update("tyres", {"id": f"eq.{current[src]['id']}"}, {"position": dst, "notes": f"{old} · {note}" if old else note})
    return vehicle_tyres(v["id"])


def edit_tyre(tyre_id: str, data: dict) -> dict:
    """Fix one tyre (its wheel, brand, size, dates, price ...): mostly for placing a tyre that came from an old bill."""
    from app.service_plan import today_th
    _require()
    try:
        tid = str(uuid.UUID(str(tyre_id)))
    except ValueError:
        raise NotFound()
    rows = supabase.select("tyres", {"select": "*", "id": f"eq.{tid}", "limit": "1"})
    if not rows:
        raise NotFound()
    t = rows[0]
    v = _vehicle(t["vehicle_id"])
    spec = _spec(v["id"])
    wheels = (spec or {}).get("wheels") or default_wheels(v)
    values: dict = {}
    if "position" in data:
        p = data.get("position") or UNKNOWN
        if p != UNKNOWN and p not in positions(wheels):
            raise InvalidTyre(f"ตำแหน่งล้อไม่ถูกต้อง: {p}")
        if p != UNKNOWN and p != t["position"] and not t.get("removed_on"):
            if any(r["position"] == p and not r.get("removed_on") and r["id"] != t["id"] for r in _rows(v["id"])):
                raise InvalidTyre(f"ล้อ{LABELS[p]}มียางอยู่แล้ว — ใช้ “บันทึกเปลี่ยนยาง” หรือ “สลับยาง”")
        values["position"] = p
    today = today_th()
    for key, what in (("fitted_on", "วันที่ใส่"), ("removed_on", "วันที่ถอด")):
        if key in data:
            values[key] = _date(data.get(key), what, today)
    for key, what in (("fitted_km", "เลขไมล์ตอนใส่"), ("removed_km", "เลขไมล์ตอนถอด")):
        if key in data:
            values[key] = _int(data.get(key), what)
    if "price" in data:
        values["price"] = _money(data.get("price"))
    for key, limit in (("brand", 60), ("vendor", 100), ("notes", 300)):
        if key in data:
            values[key] = _text(data.get(key), limit)
    if "size" in data:
        raw = _text(data.get("size"), 40)
        values["size"] = (norm_size(raw) or raw) if raw else None
    if values:
        supabase.update("tyres", {"id": f"eq.{tid}"}, values)
    return vehicle_tyres(v["id"])


def delete_tyre(tyre_id: str) -> dict:
    _require()
    try:
        tid = str(uuid.UUID(str(tyre_id)))
    except ValueError:
        raise NotFound()
    rows = supabase.select("tyres", {"select": "id,vehicle_id", "id": f"eq.{tid}", "limit": "1"})
    if not rows:
        raise NotFound()
    supabase.delete("tyres", {"id": f"eq.{tid}"})
    return vehicle_tyres(rows[0]["vehicle_id"])


def history_rows(rows: list[dict]) -> list[dict]:
    """The tyre changes as lines for the truck's history (ประวัติที่ทำแล้ว): one per day + bill, the wheels together.
    `rows` = the truck's tyres rows. Rotations are not changes and are not listed."""
    groups: dict = {}
    for r in rows:
        if not r.get("fitted_on"):
            continue
        key = (str(r["fitted_on"])[:10], r.get("event_id") or "", r.get("size") or "", r.get("brand") or "")
        groups.setdefault(key, []).append(r)
    out = []
    for (day, event_id, size, brand), rs in groups.items():
        wheels = [LABELS.get(r["position"], r["position"]) for r in sorted(rs, key=lambda r: list(LABELS).index(r["position"]) if r["position"] in LABELS else 99)]
        out.append({"done_date": day, "item_key": "tyres", "label": f"เปลี่ยนยาง {len(rs)} เส้น ({' · '.join(wheels)})",
                    "odometer_km": next((r["fitted_km"] for r in rs if r.get("fitted_km")), None), "source": "bill" if event_id else "manual",
                    "event_id": event_id or None, "notes": " ".join(x for x in (brand, size) if x) or None, "kind": "tyre"})
    return out
