"""
The repair shops (and the in-house mechanic): one canonical name per place, the spellings that mean it, who to call.

Why it exists: the same shop is written differently on every bill ("ชมรมย์การช่าง", "ชมรม", "อู่ชมรม";
"บริษัท จึงกงเฮงอีซูซุ จำกัด (ศูนย์ซ่อมสีและตัวถัง แก่งคอย)" and "อิซูซุจึงกงเฮง แก่งคอย" are the same place), so filtering
the log by shop, or adding up what was paid to one, needs them folded into one name. canonical() does that: a bill
or typed name that contains one of a shop's words (its aliases, or its own name) is that shop; when several match,
the longest word wins. A word may be a pair with "+": "มงคล+ไดนาโม" needs BOTH parts in the name (in any order), which is the
way to use a common word like "ไดนาโม" safely -- on its own it would match every dynamo shop. A name that matches nothing is
kept as written (a new shop simply appears under its own name -- nothing is ever dropped).

Two kinds live in the same list: "repair" (a place a truck is sent to -- everything above) and "supplier" (a shop the
factory buys parts from, schema_v16.sql; matched on the purchase bills the same way, see app/purchasing.py). The
car side only ever sees the repair shops.

A dealership with several departments is ONE shop: its departments are contacts (ศูนย์บริการ / ศูนย์ซ่อมสีและตัวถัง), not
separate shops. The department stays visible on the bill itself (the log shows the name as written).

The shops are kept in the database (schema_v15.sql) and changed on the settings page. The lists below are the built-in
copy: the starting point the database was filled from, and what is used when the database cannot be read (and in tests).
"""
import re
import time
from typing import Optional

from app import supabase

IN_HOUSE = "ซ่อมในโรงงาน"
MAX_NAME = 60
MAX_ALIASES = 30
MAX_CONTACTS = 12
KINDS = ("repair", "supplier")
SNAPSHOT_SECONDS = 30

# The places a truck is sent to, in the order they are offered on the board.
SHOPS: list[str] = [
    "ชมรมย์การช่าง",
    "อู่ช่างเก่ง เจริญยนต์",
    "โชคพัฒนา",
    "มงคลไดนาโม",
    "24 ไดนาโม",
    "อิซูซุจึงกงเฮงแก่งคอย",
    "ฮีโน่ เอเซีย หนองแค",
]

# (word, shop). A bill or typed name that contains the word (compared after lower-casing and removing spaces / dots /
# dashes) is that shop; the longest matching word wins. "a+b" = both a and b must be in the name.
ALIASES: list[tuple[str, str]] = [
    ("ช่างเอ๋", IN_HOUSE),
    ("ในโรงงาน", IN_HOUSE),
    ("ชมรม", "ชมรมย์การช่าง"),
    ("ช่างเก่ง", "อู่ช่างเก่ง เจริญยนต์"),
    ("เก่งเจริญ", "อู่ช่างเก่ง เจริญยนต์"),
    ("โชค", "โชคพัฒนา"),
    ("ช่างลิ้ม", "24 ไดนาโม"),
    ("ลิ้ม", "24 ไดนาโม"),
    ("มงคล+ไดนาโม", "มงคลไดนาโม"),                  # "ไดนาโม" alone is a common word (every dynamo shop): only the pair means this shop
    ("isuzu", "อิซูซุจึงกงเฮงแก่งคอย"),
    ("อีซูซุ", "อิซูซุจึงกงเฮงแก่งคอย"),
    ("อิซูซุ", "อิซูซุจึงกงเฮงแก่งคอย"),
    ("จึงกงเฮง", "อิซูซุจึงกงเฮงแก่งคอย"),
    ("hino", "ฮีโน่ เอเซีย หนองแค"),
    ("ฮีโน่", "ฮีโน่ เอเซีย หนองแค"),
    ("ฮีโน", "ฮีโน่ เอเซีย หนองแค"),
]

# Who to call. A shop can have several: the Isuzu dealership has a service centre and a body shop.
# A shop with no number yet simply shows no call button.
DIRECTORY: dict[str, dict] = {
    "ชมรมย์การช่าง": {"contacts": [{"label": "ช่างลม", "phone": "081-928-5474"}]},
    "24 ไดนาโม": {"contacts": [{"label": "ช่างลิ้ม", "phone": "081-374-0394"}]},
    IN_HOUSE: {"contacts": [{"label": "ช่างเอ๋", "phone": "085-967-4875"}]},
    "อิซูซุจึงกงเฮงแก่งคอย": {"contacts": [
        {"label": "ศูนย์บริการ", "phone": "086-391-0548"}, {"label": "ศูนย์ซ่อมสีและตัวถัง", "phone": "081-581-3465"}]},
    "ฮีโน่ เอเซีย หนองแค": {"contacts": []},
}

_STRIP = re.compile(r"[\s\-_.]")


def _norm(text: str) -> str:
    return _STRIP.sub("", (text or "").lower())


class InvalidShop(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class NotFound(Exception):
    pass


# ---------------------------------------------------------------- the data
def builtin_shops() -> list[dict]:
    """The built-in copy in the same shape as the database rows (with aliases and contacts inside)."""
    out = []
    for i, name in enumerate([*SHOPS, IN_HOUSE]):
        out.append({"id": None, "name": name, "kind": "repair", "sort_order": 99 if name == IN_HOUSE else i + 1, "active": True, "in_house": name == IN_HOUSE,
                    "aliases": [w for w, shop in ALIASES if shop == name], "contacts": list(DIRECTORY.get(name, {}).get("contacts", []))})
    return out


_snap: dict = {"at": 0.0, "shops": builtin_shops()}


def _load() -> list[dict]:
    shops = supabase.select_all("shops", {"select": "*", "order": "sort_order.asc,name.asc"})
    aliases = supabase.select_all("shop_aliases", {"select": "*", "order": "alias.asc"})
    contacts = supabase.select_all("shop_contacts", {"select": "*", "order": "sort_order.asc,label.asc"})
    return [{**s, "aliases": [a["alias"] for a in aliases if a["shop_id"] == s["id"]],
             "contacts": [{"label": c["label"], "phone": c["phone"]} for c in contacts if c["shop_id"] == s["id"]]} for s in shops]


def snapshot() -> list[dict]:
    """Every shop (active or not) with its aliases and contacts: from the database, re-read every 30 seconds; the
    built-in copy when the database is not configured or does not have the tables yet."""
    now = time.time()
    if now - _snap["at"] < SNAPSHOT_SECONDS or not supabase.configured():
        return _snap["shops"]
    try:
        rows = _load()
        _snap.update(at=now, shops=rows or builtin_shops())
    except supabase.SupabaseError:
        _snap["at"] = now                                      # keep what we had; try again in 30 seconds
    return _snap["shops"]


def refresh() -> None:
    """Forget the snapshot: the next lookup reads the database (called after every change on the settings page)."""
    _snap["at"] = 0.0


# --------------------------------------------------------------- the rules
def _parts(word: str) -> tuple:
    """A word as the parts that must ALL be present: "มงคล+ไดนาโม" -> ("มงคล", "ไดนาโม"); a plain word -> one part."""
    return tuple(p for p in (_norm(x) for x in str(word).split("+")) if p)


def _words(shops: list[dict]) -> list[tuple[tuple, str, str]]:
    """(parts, shop name, the word as written) for every shop's aliases and its own name, the most specific first
    (the most letters in all its parts)."""
    out = [(_parts(w), s["name"], w) for s in shops for w in [*s.get("aliases", []), s["name"]] if _parts(w)]
    return sorted(out, key=lambda p: -sum(len(x) for x in p[0]))


def explain(name: Optional[str], shops: Optional[list[dict]] = None) -> dict:
    """Which shop a name is, and by which word -- what the settings page's test box shows."""
    raw = (name or "").strip()
    if not raw:
        return {"shop": "", "word": None, "matched": False}
    n = _norm(raw)
    for parts, shop, word in _words(snapshot() if shops is None else shops):
        if all(p in n for p in parts):
            return {"shop": shop, "word": word, "matched": True}
    return {"shop": raw, "word": None, "matched": False}


def canonical_in(name: Optional[str], shops: list[dict]) -> str:
    return explain(name, shops)["shop"]


def of_kind(kind: str, shops: Optional[list[dict]] = None) -> list[dict]:
    return [s for s in (snapshot() if shops is None else shops) if s.get("kind", "repair") == kind]


def canonical(name: Optional[str]) -> str:
    """The repair shop for a name as written on a bill / typed by a person ('' if blank)."""
    return canonical_in(name, of_kind("repair"))


def find_supplier(name: Optional[str]) -> Optional[dict]:
    """The supplier a vendor name on a purchase bill means (by its words), or None."""
    hit = explain(name, of_kind("supplier"))
    if not hit["matched"]:
        return None
    return next((s for s in of_kind("supplier") if s["name"] == hit["shop"]), None)


def choices() -> list[str]:
    """What the board offers when sending a truck somewhere: the shops that are in use, then the in-house work."""
    shops = [s for s in of_kind("repair") if s.get("active", True)]
    return [s["name"] for s in sorted(shops, key=lambda s: (s.get("in_house", False), s.get("sort_order", 99), s["name"]))]


def directory() -> dict:
    """shop -> {contacts: [{label, phone}]} for the shops in use (what the call buttons show)."""
    return {s["name"]: {"contacts": s.get("contacts", [])} for s in snapshot() if s.get("active", True)}


def board_places() -> list[dict]:
    """The shops as places for the manager's board (the in-house work is not somewhere you go)."""
    return [{"id": s.get("id"), "label": s["name"], "kind": "shop", "sort_order": 10 + i, "active": True}
            for i, s in enumerate(x for x in snapshot() if x.get("active", True) and not x.get("in_house"))]   # repair shops and suppliers: both are places to go


# --------------------------------------------------------------- validation
def _text(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def clean_shop(data: dict, *, existing: Optional[dict] = None, others: list[dict]) -> dict:
    """The values to write for a new shop (existing=None) or for the fields present in `data`. `others` = every OTHER
    shop with its aliases (a name or a word may belong to one shop only)."""
    creating = existing is None
    out: dict = {}
    if creating or "name" in data:
        name = _text(data.get("name"), MAX_NAME)
        if not name:
            raise InvalidShop("ใส่ชื่อร้านด้วย")
        if any(_norm(o["name"]) == _norm(name) for o in others):
            raise InvalidShop(f"มีร้านชื่อ “{name}” อยู่แล้ว")
        out["name"] = name
    if creating:
        kind = data.get("kind") or "repair"
        if kind not in KINDS:
            raise InvalidShop("ประเภทร้านไม่ถูกต้อง")
        out["kind"] = kind
    if "active" in data:
        out["active"] = bool(data["active"])
    if "sort_order" in data:
        try:
            out["sort_order"] = max(0, min(999, int(data["sort_order"])))
        except (TypeError, ValueError):
            raise InvalidShop("ลำดับต้องเป็นตัวเลข")
    if "aliases" in data:
        raw = data["aliases"]
        if not isinstance(raw, list):
            raise InvalidShop("คำที่ใช้เรียกร้านไม่ถูกต้อง")
        words, seen = [], set()
        mine = _norm(out.get("name") or (existing or {}).get("name") or "")
        for w in raw:
            parts = [p.strip() for p in _text(w, MAX_NAME).split("+") if p.strip()]
            word = "+".join(parts)
            key = "+".join(sorted(_norm(p) for p in parts))
            if not key or key in seen or key == mine:
                continue
            clash = next((o["name"] for o in others if key == _norm(o["name"]) or key in {"+".join(sorted(_parts(a))) for a in o.get("aliases", [])}), None)
            if clash:
                raise InvalidShop(f"คำว่า “{word}” เป็นของร้าน “{clash}” อยู่แล้ว")
            seen.add(key)
            words.append(word)
        if len(words) > MAX_ALIASES:
            raise InvalidShop(f"ใส่คำได้ไม่เกิน {MAX_ALIASES} คำ")
        out["aliases"] = words
    if "contacts" in data:
        raw = data["contacts"]
        if not isinstance(raw, list):
            raise InvalidShop("รายชื่อติดต่อไม่ถูกต้อง")
        contacts = []
        for c in raw:
            label, phone = _text((c or {}).get("label"), 40), _text((c or {}).get("phone"), 30)
            if not label and not phone:
                continue
            if not label:
                raise InvalidShop("ใส่ชื่อแผนก / ชื่อคนด้วย (เช่น ศูนย์บริการ)")
            if not re.fullmatch(r"[0-9+\-\s()]{5,30}", phone):
                raise InvalidShop(f"เบอร์โทรของ “{label}” ไม่ถูกต้อง")
            contacts.append({"label": label, "phone": phone})
        if len(contacts) > MAX_CONTACTS:
            raise InvalidShop(f"ใส่เบอร์ได้ไม่เกิน {MAX_CONTACTS} เบอร์")
        out["contacts"] = contacts
    return out


# ----------------------------------------------------------------- database
_probe = {"at": 0.0, "ok": False}
NEEDS_V15 = "ต้องรัน schema_v15.sql ใน Supabase ก่อน จึงจะแก้ร้านในหน้าตั้งค่าได้"


def has_v15() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        for table in ("shops", "shop_aliases", "shop_contacts"):
            supabase.select(table, {"select": "id", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not has_v15():
        raise InvalidShop(NEEDS_V15)


def settings() -> dict:
    """The settings page: every shop (also the ones switched off) with its words and contacts."""
    ready = supabase.configured() and has_v15()
    return {"ready": ready, "shops": _load() if ready else builtin_shops()}


def _save_children(shop_id: str, values: dict) -> None:
    if "aliases" in values:
        supabase.delete("shop_aliases", {"shop_id": f"eq.{shop_id}"})
        if values["aliases"]:
            supabase.insert("shop_aliases", [{"shop_id": shop_id, "alias": w} for w in values["aliases"]])
    if "contacts" in values:
        supabase.delete("shop_contacts", {"shop_id": f"eq.{shop_id}"})
        if values["contacts"]:
            supabase.insert("shop_contacts", [{"shop_id": shop_id, "label": c["label"], "phone": c["phone"], "sort_order": i + 1} for i, c in enumerate(values["contacts"])])


def create(data: dict) -> dict:
    _require()
    current = _load()
    values = clean_shop(data, others=current)
    row = {"name": values["name"], "sort_order": values.get("sort_order", 50), "active": values.get("active", True)}
    if values["kind"] != "repair":
        row["kind"] = values["kind"]                         # the column exists from schema_v16
    row = supabase.insert("shops", [row])[0]
    _save_children(row["id"], values)
    refresh()
    return settings()


def update(shop_id: str, data: dict) -> dict:
    _require()
    current = _load()
    existing = next((s for s in current if s["id"] == shop_id), None)
    if existing is None:
        raise NotFound()
    values = clean_shop(data, existing=existing, others=[s for s in current if s["id"] != shop_id])
    row_values = {k: values[k] for k in ("name", "active", "sort_order") if k in values}
    if "name" in values and values["name"] != existing["name"]:
        # a renamed shop keeps what it used to be called as a word, and the work already recorded under the old name follows it
        old = existing["name"]
        aliases = values.get("aliases", existing["aliases"])
        if not any(_norm(a) == _norm(old) for a in aliases) and _norm(old) != _norm(values["name"]):
            values["aliases"] = [*aliases, old]
        supabase.update("repair_requests", {"shop": f"eq.{old}"}, {"shop": values["name"]})
        try:
            supabase.update("mgr_tickets", {"place": f"eq.{old}"}, {"place": values["name"]})
        except supabase.SupabaseError:
            pass                                                 # the manager's board is optional (schema_v12)
    if row_values:
        supabase.update("shops", {"id": f"eq.{shop_id}"}, row_values)
    _save_children(shop_id, values)
    refresh()
    return settings()
