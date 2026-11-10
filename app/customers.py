"""
ทะเบียนลูกค้า: the customer register (owner, 10 Nov 2026).

The Drive "Customer Info" sheet, tab สระบุรี, has one row per customer who borrowed a bin (CUST_ID = the Drive folder "N_name" with
their ID card / form photos): shop, contact name, phone, ID-card number, GPS, the driver and truck, the loan date, up to four bins.
It is imported once (scripts/import_customer_register.py) and kept here, linked to the route customer (sale_route_customers) it is:

  1. same truck, a bin in common -> that customer (several: the closest shop name decides)        match = "truck+bin" / "truck+bin+name"
  2. same truck, the shop name -> that customer (only when exactly one fits)                        match = "name"
  3. the bin is on another truck AND the shop name is close -> that customer (the shop changed truck) match = "bin+name"
  4. anything else is NOT guessed: it waits in the review list with the likely ones as suggestions, and the office links it by
     hand (match = "manual"). A re-import never overwrites a manual link.

ID-card numbers and photos are only ever shown on one customer's own page, to the owner and the people given this menu; never in a
list, an export or on the driver's phone. Photos are in a private storage bucket (supabase.PRIVATE_BUCKET).
"""
import re
from datetime import date, datetime
from typing import Optional

BRANCH_SLUG = {"สระบุรี": "sbr", "แก่งคอย": "kk"}


class CustomerError(ValueError):
    """Refused, with a message shown as it is."""


# --------------------------------------------------------------------------------------------- reading the sheet (pure)
def norm_no(x) -> str:
    """6.0 / '6' / ' 06 ' -> '6'; '0412' -> '412'; '' -> ''."""
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    s = str(x if x is not None else "").strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    s = re.sub(r"\D", "", s)
    return str(int(s)) if s else ""


def bin_tokens(text: Optional[str]) -> set:
    """'V200-1805, S200-0412' -> {('200', '1805'), ('200', '412')} (the letter is the bin model, not part of its number)."""
    return {(m.group(2), str(int(m.group(3)))) for m in re.finditer(r"([A-Za-z]*)\s*(\d{2,3})\s*-\s*(\d+)", text or "")}


def be_date(x) -> Optional[str]:
    """'20/09/2568' (พ.ศ.) / a date -> '2025-09-20'."""
    if isinstance(x, datetime):
        return x.date().isoformat()
    if isinstance(x, date):
        return x.isoformat()
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", str(x or ""))
    if not m:
        return None
    y = int(m.group(3))
    y = y - 543 if y > 2400 else y
    try:
        return date(y, int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def _text(x) -> Optional[str]:
    s = re.sub(r"\s+", " ", str(x if x is not None else "")).strip()
    return s or None


def register_rows(rows: list) -> list[dict]:
    """The สระบุรี tab's rows (first = headers): CUST_ID, Location, ชื่อพขร, เบอร์รถ, ยืมวันที่, (ถังขนาด n, ถังเบอร์ n) x4, -, ชื่อร้าน,
    ชื่อลูกค้า, เบอร์โทรศัพท์, เลขที่บัตรประชาชน, หมายเหตุ."""
    out = []
    for r in rows[1:]:
        r = list(r) + [None] * 20
        try:
            no = int(float(str(r[0]).strip()))
        except (TypeError, ValueError):
            continue
        bins = []
        for i in range(4):
            size, num = norm_no(r[5 + 2 * i]), norm_no(r[6 + 2 * i])
            if size and num:
                bins.append({"size": size, "no": num})
        phone = _text(r[16])
        out.append({"cust_no": no, "gps": _text(r[1]), "driver_name": _text(r[2]), "truck_no": norm_no(r[3]) or None,
                    "loan_date": be_date(r[4]), "bins": bins, "shop": _text(r[14]), "contact_name": _text(r[15]),
                    "phone": phone, "id_card": _text(r[17]), "note": _text(r[18])})
    return out


# --------------------------------------------------------------------------------------------- matching (pure)
def _clean(s: Optional[str]) -> str:
    s = re.sub(r"[\s\.\-\(\)/,&+'\"]", "", (s or "").lower())
    return re.sub(r"^ร้าน|ร้าน", "", s)


def name_score(a: Optional[str], b: Optional[str]) -> float:
    """0..1: how alike two shop names are (one inside the other = 1; else shared letter pairs)."""
    x, y = _clean(a), _clean(b)
    if not x or not y or x in ("บุคคล",) or y in ("บุคคล",):
        return 0.0
    if x in y or y in x:
        return 1.0
    bx, by = {x[i:i + 2] for i in range(len(x) - 1)}, {y[i:i + 2] for i in range(len(y) - 1)}
    return 2 * len(bx & by) / (len(bx) + len(by)) if bx and by else 0.0


def match(reg: dict, customers: list[dict], truck_of: dict) -> dict:
    """reg = one register row; customers = route customers {id, route_id, seq, name, bins}; truck_of = {route_id: truck_no}.
    -> {"customer_id", "match", "suggest": [ids]}."""
    bins = {(b["size"], b["no"]) for b in reg["bins"]}
    shop = reg.get("shop") if (reg.get("shop") or "") not in ("บุคคล", "-") else None
    names = [n for n in (shop, reg.get("contact_name")) if n]
    same = [c for c in customers if truck_of.get(c["route_id"]) == reg.get("truck_no")]
    score = lambda c: max([name_score(n, c["name"]) for n in names] or [0.0])        # noqa: E731

    hit = [c for c in same if bins & bin_tokens(c.get("bins"))]
    if len(hit) == 1:
        return {"customer_id": hit[0]["id"], "match": "truck+bin", "suggest": []}
    if len(hit) > 1:
        best = sorted(hit, key=score, reverse=True)
        if score(best[0]) >= 0.5 and score(best[0]) > score(best[1]):
            return {"customer_id": best[0]["id"], "match": "truck+bin+name", "suggest": []}
        return {"customer_id": None, "match": None, "suggest": [c["id"] for c in best][:5]}
    by_name = [c for c in same if shop and name_score(shop, c["name"]) == 1.0]
    if len(by_name) == 1:
        return {"customer_id": by_name[0]["id"], "match": "name", "suggest": []}
    elsewhere = [c for c in customers if bins & bin_tokens(c.get("bins"))]
    close = [c for c in elsewhere if score(c) >= 0.6]
    if len(close) == 1:
        return {"customer_id": close[0]["id"], "match": "bin+name", "suggest": []}
    sugg = elsewhere + by_name + sorted([c for c in same if score(c) >= 0.4], key=score, reverse=True)
    seen, ids = set(), []
    for c in sugg:
        if c["id"] not in seen:
            seen.add(c["id"])
            ids.append(c["id"])
    return {"customer_id": None, "match": None, "suggest": ids[:5]}


# --------------------------------------------------------------------------------------------- import (scripts/import_customer_register.py)
def import_register(rows: list[dict], branch: str = "สระบุรี", write: bool = False) -> dict:
    from app import supabase
    routes = supabase.select_all("sale_routes", {"select": "id,truck_no", "order": "id.asc"})
    truck_of = {r["id"]: r["truck_no"] for r in routes}
    custs = supabase.select_all("sale_route_customers", {"select": "id,route_id,seq,name,bins", "active": "eq.true", "order": "id.asc"})
    manual = {r["cust_no"]: r for r in supabase.select_all("customer_register", {"select": "cust_no,customer_id,match",
                                                                                   "branch": f"eq.{branch}", "match": "eq.manual",
                                                                                   "order": "cust_no.asc"})}
    out, counts = [], {}
    for g in rows:
        m = {"customer_id": manual[g["cust_no"]]["customer_id"], "match": "manual", "suggest": []} if g["cust_no"] in manual \
            else match(g, custs, truck_of)
        counts[m["match"] or "review"] = counts.get(m["match"] or "review", 0) + 1
        out.append({**g, **m, "branch": branch, "updated_at": datetime.utcnow().isoformat() + "Z"})
    if write:
        for i in range(0, len(out), 200):
            supabase.insert("customer_register", out[i:i + 200], on_conflict="branch,cust_no")
    return {"rows": len(out), "counts": counts, "review": [r for r in out if not r["customer_id"]]}


def photo_path(branch: str, cust_no: int, digest: str, ext: str) -> str:
    return f"register/{BRANCH_SLUG.get(branch, 'x')}/{cust_no}/{digest}.{ext}"


def shrink(data: bytes, mime: str) -> tuple[bytes, str, str]:
    """A phone photo (4-12 MB) -> at most 2000 px JPEG, still sharp enough to read an ID card. PDFs stay as they are."""
    if mime == "application/pdf":
        return data, mime, "pdf"
    from io import BytesIO

    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(BytesIO(data)))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((2000, 2000))
    buf = BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue(), "image/jpeg", "jpg"


# --------------------------------------------------------------------------------------------- the office page
def name_ok(reg: dict, route_name: Optional[str]) -> bool:
    """Does the register's shop (or person) name agree with the route list's name? Spelling differences pass (ป้าแน๊ต / ป้าแน็ต);
    a different shop, or a register row with only a person's name, does not -- the bin may have moved (owner, 10 Nov 2026)."""
    shop = reg.get("shop") if (reg.get("shop") or "") not in ("บุคคล", "-") else None
    return max([name_score(n, route_name) for n in (shop, reg.get("contact_name")) if n] or [0.0]) >= 0.6


def _route_names() -> dict:
    from app import supabase
    return {r["id"]: r for r in supabase.select_all("sale_routes", {"select": "id,truck_no,name,driver_name", "order": "id.asc"})}


def _custs(ids: set) -> dict:
    from app import supabase
    if not ids:
        return {}
    out = {}
    idl = sorted(ids)
    for i in range(0, len(idl), 150):
        for c in supabase.select("sale_route_customers", {"select": "id,route_id,seq,name,bins,prices,active",
                                                          "id": f"in.({','.join(str(x) for x in idl[i:i + 150])})"}):
            out[c["id"]] = c
    return out


def _cust_view(c: Optional[dict], routes: dict) -> Optional[dict]:
    if not c:
        return None
    r = routes.get(c["route_id"]) or {}
    return {"id": c["id"], "truck_no": r.get("truck_no"), "route": r.get("name"), "seq": c["seq"], "name": c["name"], "bins": c.get("bins"),
            "active": c.get("active", True)}


def list_register(branch: str = "สระบุรี") -> dict:
    """Every register row for the list (no ID numbers, no phone), with the route customer it is linked to or might be."""
    from app import supabase
    rows = supabase.select_all("customer_register", {"select": "cust_no,customer_id,match,suggest,shop,contact_name,truck_no,driver_name,"
                                                                "loan_date,bins,gps,note", "branch": f"eq.{branch}", "order": "cust_no.asc"})
    photos: dict = {}
    for p in supabase.select_all("customer_photos", {"select": "cust_no", "branch": f"eq.{branch}", "order": "id.asc"}):
        photos[p["cust_no"]] = photos.get(p["cust_no"], 0) + 1
    ids = {r["customer_id"] for r in rows if r["customer_id"]} | {i for r in rows for i in (r["suggest"] or [])}
    custs, routes = _custs(ids), _route_names()
    for r in rows:
        r["customer"] = _cust_view(custs.get(r["customer_id"]), routes)
        r["suggest"] = [_cust_view(custs.get(i), routes) for i in r["suggest"] or [] if custs.get(i)]
        r["photos"] = photos.get(r["cust_no"], 0)
        # linked by the bin but the names do not agree: someone has to look (a hand-made link counts as looked at)
        r["check"] = bool(r["customer"]) and r["match"] != "manual" and not name_ok(r, r["customer"]["name"])
    return {"branch": branch, "rows": rows, "linked": sum(1 for r in rows if r["customer_id"]),
            "review": sum(1 for r in rows if not r["customer_id"]), "check": sum(1 for r in rows if r["check"])}


def detail(branch: str, cust_no: int) -> dict:
    from app import supabase
    r = supabase.select("customer_register", {"select": "*", "branch": f"eq.{branch}", "cust_no": f"eq.{int(cust_no)}", "limit": "1"})
    if not r:
        raise CustomerError("ไม่พบลูกค้านี้ในทะเบียน")
    r = r[0]
    routes = _route_names()
    custs = _custs({r["customer_id"]} if r["customer_id"] else set())
    r["customer"] = _cust_view(custs.get(r["customer_id"]), routes)
    r["check"] = bool(r["customer"]) and r["match"] != "manual" and not name_ok(r, r["customer"]["name"])
    r["photos"] = supabase.select_all("customer_photos", {"select": "id,name,mime_type,byte_size", "branch": f"eq.{branch}",
                                                          "cust_no": f"eq.{int(cust_no)}", "order": "id.asc"})
    return r


def link(branch: str, cust_no: int, customer_id: Optional[int]) -> dict:
    """The office links a register row to a route customer by hand (or unlinks it: None)."""
    from app import supabase
    if customer_id is not None and not supabase.select("sale_route_customers", {"select": "id", "id": f"eq.{int(customer_id)}", "limit": "1"}):
        raise CustomerError("ไม่พบลูกค้าในสายนี้")
    rows = supabase.update("customer_register", {"branch": f"eq.{branch}", "cust_no": f"eq.{int(cust_no)}"},
                           {"customer_id": customer_id, "match": "manual" if customer_id else None,
                            "updated_at": datetime.utcnow().isoformat() + "Z"})
    if not rows:
        raise CustomerError("ไม่พบลูกค้านี้ในทะเบียน")
    return {"ok": True}


def route_customers(truck_no: str) -> list[dict]:
    """A truck's customers, to pick from when linking by hand."""
    from app import supabase
    r = supabase.select("sale_routes", {"select": "id,truck_no,name", "truck_no": f"eq.{truck_no}", "limit": "1"})
    if not r:
        return []
    routes = {r[0]["id"]: r[0]}
    return [_cust_view(c, routes) for c in supabase.select_all("sale_route_customers", {"select": "id,route_id,seq,name,bins,active",
                                                                                        "route_id": f"eq.{r[0]['id']}", "active": "eq.true",
                                                                                        "order": "seq.asc"})]


def photo(photo_id: int) -> tuple[bytes, str]:
    from app import supabase
    p = supabase.select("customer_photos", {"select": "storage_path,mime_type", "id": f"eq.{int(photo_id)}", "limit": "1"})
    if not p:
        raise CustomerError("ไม่พบรูปนี้")
    return supabase.storage_download(p[0]["storage_path"], bucket=supabase.PRIVATE_BUCKET), p[0]["mime_type"] or "image/jpeg"
