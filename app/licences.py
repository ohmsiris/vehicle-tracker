"""
ใบอนุญาต · เอกสาร (#licences) -- the company's licences and certificates, and the drivers' licences, in one register (owner, 12 Nov 2026).

Each row is one document at one place (ภพ.20 has three: สำนักงานใหญ่, สาขา 1, สาขา 2) or of one driver. A new copy is scanned and uploaded;
the dates are read off it by Claude and confirmed by the owner; the older copies stay as history (never deleted). The page shows what
has expired, what falls due within its warning time, and what still has no file. "ยื่นแล้ว รอใบใหม่" holds the reminders while the new
paper is on its way. The dates also go to the owner's Google Calendar through a private calendar feed (.ics) it subscribes to.
Only the owner sees this page for now. Tables: company_docs, company_doc_files (schema_v74.sql). The pure parts are unit-tested.
"""
import calendar
import hashlib
import hmac
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("licences")

COMPANY = "บริษัท สระบุรีวารีเทพ จำกัด"
PLACES = ["สำนักงานใหญ่", "สาขา 1", "สาขา 2"]      # สำนักงานใหญ่ + สาขา 1 at สระบุรี (72/9, 72 ม.8 ตลิ่งชัน); สาขา 2 = แก่งคอย (150 ม.8 บ้านป่า)
PLACE_NOTE = {"สำนักงานใหญ่": "สระบุรี", "สาขา 1": "สระบุรี", "สาขา 2": "แก่งคอย"}

# key -> label, how often it comes round (months; None = it does not, or the paper says), days of warning, how the next date is found
KINDS = {
    "certificate": {"label": "หนังสือรับรองบริษัท", "months": 3, "warn": 14, "from_issue": True, "note": "อายุ 3 เดือน · ขอใหม่ตลอด"},
    "pp20": {"label": "ภพ.20", "months": None, "warn": 0, "expires": False, "note": "ไม่มีวันหมดอายุ"},
    "rng4": {"label": "รง.4", "months": 12, "warn": 30, "note": "ต่อทุกปี"},
    "hazard": {"label": "ใบอนุญาตกิจการที่เป็นอันตรายต่อสุขภาพ", "months": 12, "warn": 30, "note": "ต่อทุกปี"},
    "o2": {"label": "อ.2 ใบอนุญาตผลิตอาหาร", "months": 36, "warn": 60, "note": "อายุ 3 ปี"},
    "food_controller": {"label": "ผู้ควบคุมการผลิตอาหาร", "months": None, "warn": 30, "note": ""},
    "gmp": {"label": "ใบรับรอง GMP", "months": None, "warn": 90, "note": ""},
    "land_tax": {"label": "ภาษีที่ดินและสิ่งปลูกสร้าง", "months": 12, "warn": 30, "pay": True, "note": "จ่ายทุกปี · เก็บใบเสร็จ"},
    "sign_tax": {"label": "ภาษีป้าย", "months": 12, "warn": 30, "pay": True, "note": "จ่ายทุกปี · เก็บใบเสร็จ"},
    "driving": {"label": "ใบขับขี่", "months": None, "warn": 60, "note": ""},
    "other": {"label": "เอกสารอื่น", "months": None, "warn": 30, "note": ""},
}
SECTIONS = [("company", "เอกสารบริษัท"), ("driving", "ใบขับขี่")]
MAX_FILE = 25 * 1024 * 1024
READ_MODEL = "claude-opus-5-5"


class LicenceError(ValueError):
    """Refused, with a message shown as it is."""


# --------------------------------------------------------------------------------------------- dates and status (pure)
def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _d(v) -> Optional[date]:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def kind_of(doc: dict) -> dict:
    return KINDS.get(doc.get("kind") or "", KINDS["other"])


def status(doc: dict, today: date) -> dict:
    """{state, days, warn}: expired | soon | submitted (ยื่นแล้ว รอใบใหม่) | ok | nodate (should have a date, has none) | never."""
    k = kind_of(doc)
    warn = doc.get("warn_days") if doc.get("warn_days") is not None else k["warn"]
    exp = _d(doc.get("expires_on"))
    if not exp:
        return {"state": "never" if k.get("expires") is False else "nodate", "days": None, "warn": warn}
    days = (exp - today).days
    if doc.get("submitted_on"):                      # cleared when the new paper is uploaded
        state = "submitted"
    elif days < 0:
        state = "expired"
    elif days <= warn:
        state = "soon"
    else:
        state = "ok"
    return {"state": state, "days": days, "warn": warn}


def suggest(kind: str, read: dict, current: dict) -> dict:
    """The dates to offer after a new copy is uploaded: what the paper says; a หนังสือรับรอง runs 3 months from its date; a yearly
    payment (ภาษี) moves its due date on a year when the receipt shows none."""
    k = KINDS.get(kind, KINDS["other"])
    issued, exp = _d(read.get("issued_on")), _d(read.get("expires_on"))
    if not exp and k.get("from_issue") and issued and k["months"]:
        exp = add_months(issued, k["months"])
    if not exp and k.get("pay") and _d(current.get("expires_on")):
        exp = add_months(_d(current["expires_on"]), k["months"])
    return {"issued_on": issued.isoformat() if issued else None, "expires_on": exp.isoformat() if exp else None,
            "doc_no": (str(read.get("doc_no") or "").strip()[:60] or None)}


def clean(body: dict, *, new: bool) -> dict:
    out: dict = {}
    if new or "kind" in body:
        kind = str(body.get("kind") or "")
        if kind not in KINDS:
            raise LicenceError("ไม่รู้จักประเภทเอกสารนี้")
        out["kind"] = kind
    for k, n in (("title", 120), ("place", 60), ("doc_no", 60), ("note", 500), ("company", 120)):
        if k in body:
            out[k] = str(body[k] or "").strip()[:n] or None
    for k in ("issued_on", "expires_on", "submitted_on"):
        if k in body:
            v = body[k]
            if v in (None, ""):
                out[k] = None
            elif _d(v) is None:
                raise LicenceError("วันที่ไม่ถูกต้อง")
            else:
                out[k] = _d(v).isoformat()
    if "warn_days" in body:
        v = body["warn_days"]
        out["warn_days"] = None if v in (None, "") else max(0, min(int(float(v)), 730))
    if "employee_id" in body:
        out["employee_id"] = str(body["employee_id"] or "").strip() or None
    if new:
        out.setdefault("title", None)
        if not out["title"]:
            out["title"] = KINDS[out["kind"]]["label"]
        out.setdefault("company", COMPANY)
        out["company"] = out["company"] or COMPANY
    return out


def view(docs: list[dict], files: list[dict], people: dict, today: date) -> dict:
    """The page: tiles, ต้องทำตอนนี้ (expired, due within its warning, waiting for a new paper, no date yet), then each section."""
    by_doc: dict = {}
    for f in files:
        by_doc.setdefault(f["doc_id"], []).append(f)
    rows = []
    for d in docs:
        k = kind_of(d)
        fl = sorted(by_doc.get(d["id"], []), key=lambda f: (not f.get("current"), f.get("uploaded_at") or ""), reverse=False)
        cur = next((f for f in fl if f.get("current")), None)
        st = status(d, today)
        p = people.get(d.get("employee_id")) or {}
        rows.append({**{x: d.get(x) for x in ("id", "kind", "title", "company", "place", "employee_id", "doc_no", "issued_on", "expires_on",
                                              "warn_days", "submitted_on", "note")},
                     "kind_label": k["label"], "kind_note": k["note"], "place_note": PLACE_NOTE.get(d.get("place") or ""),
                     "person": p.get("name"), "nickname": p.get("nickname"), "truck": p.get("truck"),
                     "section": "driving" if d["kind"] == "driving" else "company", **st,
                     "file": {"id": cur["id"], "name": cur.get("name"), "uploaded_at": cur.get("uploaded_at")} if cur else None,
                     "old_files": [{"id": f["id"], "name": f.get("name"), "uploaded_at": f.get("uploaded_at")} for f in fl if not f.get("current")]})
    act = [r for r in rows if r["state"] in ("expired", "soon", "submitted", "nodate")]
    act.sort(key=lambda r: (r["days"] if r["days"] is not None else 10_000, r["title"]))
    acting = {r["id"] for r in act}

    def sect_key(r):
        return (r["days"] if r["days"] is not None else 99_999, r["truck"] or "", r["title"]) if r["section"] == "driving" \
            else (list(KINDS).index(r["kind"]) if r["kind"] in KINDS else 99,
                  PLACES.index(r["place"]) if r["place"] in PLACES else 9, r["title"])
    sections = [{"key": key, "label": lab, "rows": sorted([r for r in rows if r["section"] == key and r["id"] not in acting], key=sect_key),
                 "count": sum(1 for r in rows if r["section"] == key)} for key, lab in SECTIONS]
    tiles = {"expired": sum(1 for r in rows if r["state"] == "expired"),
             "soon": sum(1 for r in rows if r["state"] in ("soon", "submitted")),
             "nofile": sum(1 for r in rows if not r["file"]),
             "ok": sum(1 for r in rows if r["state"] in ("ok", "never"))}
    return {"today": today.isoformat(), "tiles": tiles, "act": act, "sections": sections, "count": len(rows)}


# --------------------------------------------------------------------------------------------- the calendar feed (pure)
def feed_key(secret: str) -> str:
    return hmac.new((secret or "").encode(), b"icebox-licences-ics", hashlib.sha256).hexdigest()[:32]


def _esc(s: str) -> str:
    return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(docs: list[dict], people: dict, now: datetime) -> str:
    """Every dated document: an all-day event on its date, and a reminder its warning time before."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//IceBox//licences//TH", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:IceBox ใบอนุญาต · เอกสาร", "X-WR-TIMEZONE:Asia/Bangkok", "REFRESH-INTERVAL;VALUE=DURATION:PT12H"]

    def event(uid: str, day: date, summary: str, desc: str):
        lines.extend(["BEGIN:VEVENT", f"UID:{uid}@icebox", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}", f"SUMMARY:{_esc(summary)}", f"DESCRIPTION:{_esc(desc)}",
                      "TRANSP:TRANSPARENT", "END:VEVENT"])
    for d in docs:
        exp = _d(d.get("expires_on"))
        if not exp:
            continue
        k = kind_of(d)
        who = (people.get(d.get("employee_id")) or {}).get("name")
        name = " · ".join(x for x in (d["title"], who if who and who not in d["title"] else None, d.get("place")) if x)
        verb = "ครบกำหนดจ่าย" if k.get("pay") else "หมดอายุ"
        desc = "IceBox › ใบอนุญาต · เอกสาร" + (" · เลขที่ " + d["doc_no"] if d.get("doc_no") else "")
        event(f"doc{d['id']}-{exp:%Y%m%d}", exp, f"{verb}: {name}", desc)
        warn = d.get("warn_days") if d.get("warn_days") is not None else k["warn"]
        if warn:
            event(f"doc{d['id']}-{exp:%Y%m%d}-warn", exp - timedelta(days=warn), f"เตือน: {'จ่าย' if k.get('pay') else 'ต่อ'} {name} (อีก {warn} วัน)",
                  f"{verb} {exp.day:02d}/{exp.month:02d}/{exp.year}")
    lines.append("END:VCALENDAR")
    out = []
    for ln in lines:                                   # fold long lines at 75 octets (RFC 5545)
        b = ln.encode()
        while len(b) > 75:
            cut = 75
            while cut > 0 and (b[cut] & 0xC0) == 0x80:
                cut -= 1
            out.append(b[:cut].decode())
            b = b" " + b[cut:]
        out.append(b.decode())
    return "\r\n".join(out) + "\r\n"


# --------------------------------------------------------------------------------------------- reading a scan
READ_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "doc_type": {"type": "string", "description": "what the document is, in Thai as printed (e.g. ภ.พ.20, หนังสือรับรอง, ใบอนุญาตขับรถ)"},
        "doc_no": {"type": ["string", "null"]},
        "issued_on": {"type": ["string", "null"], "description": "YYYY-MM-DD, Gregorian"},
        "expires_on": {"type": ["string", "null"], "description": "YYYY-MM-DD, Gregorian; the last day it is valid, or the payment due date"},
        "holder_name": {"type": ["string", "null"]},
        "place": {"type": ["string", "null"], "description": "สำนักงานใหญ่ / สาขาที่ ... and the address, as printed"},
    },
    "required": ["doc_type", "doc_no", "issued_on", "expires_on", "holder_name", "place"],
}
READ_PROMPT = """You read Thai official documents (licences, certificates, tax receipts, driving licences) for a company's register.
Return the document type, its number, the date it was issued, the date it expires (the last day it is valid; for a tax receipt the
next due date if printed, else null), the holder's name and the place (head office / branch number and address) as printed.
Thai dates use the Buddhist Era: subtract 543 from the year (2569 -> 2026). Thai month names: มกราคม=01 กุมภาพันธ์=02 มีนาคม=03
เมษายน=04 พฤษภาคม=05 มิถุนายน=06 กรกฎาคม=07 สิงหาคม=08 กันยายน=09 ตุลาคม=10 พฤศจิกายน=11 ธันวาคม=12.
Give dates as YYYY-MM-DD in the Gregorian calendar. Use null for anything not printed; never guess."""


def read_scan(data: bytes, mime: str) -> dict:
    from app.extraction import _client_lazy, _content_block
    r = _client_lazy().beta.messages.create(
        model=READ_MODEL, max_tokens=4000, system=[{"type": "text", "text": READ_PROMPT}],
        messages=[{"role": "user", "content": [_content_block(data, mime), {"type": "text", "text": "Read this document."}]}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    return json.loads(next(b.text for b in r.content if b.type == "text"))


def mime_of(name: str, data: bytes) -> str:
    n = (name or "").lower()
    if n.endswith(".pdf") or data[:4] == b"%PDF":
        return "application/pdf"
    if n.endswith(".png") or data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if n.endswith(".webp"):
        return "image/webp"
    if n.endswith((".jpg", ".jpeg")) or data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    raise LicenceError("รับเฉพาะรูป (JPG / PNG) หรือ PDF")


# --------------------------------------------------------------------------------------------- the database
def _people() -> dict:
    from app import supabase
    from app.roster import load_roster
    truck = {(v.get("driver") or "").strip(): v.get("truck_number") for v in load_roster() if v.get("driver")}
    out = {}
    for e in supabase.select_all("employees", {"select": "id,full_name,nickname,position,branch,status", "order": "full_name.asc"}):
        out[e["id"]] = {"name": e["full_name"], "nickname": e.get("nickname") or "", "truck": truck.get((e["full_name"] or "").strip()),
                        "position": e.get("position") or "", "active": e.get("status") == "active"}
    return out


def _docs() -> list[dict]:
    from app import supabase
    return supabase.select_all("company_docs", {"select": "*", "active": "eq.true", "order": "id.asc"})


def load(today: date) -> dict:
    from app import supabase
    docs, people = _docs(), _people()
    files = supabase.select_all("company_doc_files", {"select": "id,doc_id,name,current,uploaded_at", "order": "id.asc"})
    out = view(docs, files, people, today)
    drivers = sorted([{"id": k, "name": v["name"], "nickname": v["nickname"], "truck": v["truck"]} for k, v in people.items()
                      if v["active"] and "ขับรถ" in v["position"]], key=lambda x: x["name"])
    return {**out, "kinds": [{"key": k, "label": v["label"], "note": v["note"]} for k, v in KINDS.items()], "places": PLACES,
            "drivers": drivers}


def for_employee(employee_id: str, today: date) -> list[dict]:
    from app import supabase
    docs = supabase.select_all("company_docs", {"select": "*", "employee_id": f"eq.{employee_id}", "active": "eq.true", "order": "id.asc"})
    return [{"id": d["id"], "title": d["title"], "expires_on": d.get("expires_on"), **status(d, today)} for d in docs]


def _get(doc_id: int) -> dict:
    from app import supabase
    d = supabase.select("company_docs", {"select": "*", "id": f"eq.{int(doc_id)}", "limit": "1"})
    if not d:
        raise LicenceError("ไม่พบเอกสารนี้")
    return d[0]


def create(body: dict) -> dict:
    from app import supabase
    values = clean(body, new=True)
    if values["kind"] == "driving" and not values.get("employee_id"):
        raise LicenceError("เลือกคนขับก่อน")
    return supabase.insert("company_docs", [values])[0]


def update(doc_id: int, body: dict) -> dict:
    from app import supabase
    _get(doc_id)
    values = clean(body, new=False)
    if not values:
        raise LicenceError("ไม่มีอะไรเปลี่ยน")
    values["updated_at"] = datetime.now(timezone.utc).isoformat()
    return supabase.update("company_docs", {"id": f"eq.{int(doc_id)}"}, values)[0]


def remove(doc_id: int) -> dict:
    """Taken off the list; its files stay in storage (never deleted)."""
    from app import supabase
    _get(doc_id)
    supabase.update("company_docs", {"id": f"eq.{int(doc_id)}"}, {"active": False, "updated_at": datetime.now(timezone.utc).isoformat()})
    return {"ok": True}


def add_file(doc_id: int, name: str, data: bytes, who: str, read: bool = True) -> dict:
    """A new copy: kept, made the current one (the older copy stays as history), and read for its dates."""
    from app import supabase
    doc = _get(doc_id)
    if not data:
        raise LicenceError("ไฟล์ว่าง")
    if len(data) > MAX_FILE:
        raise LicenceError("ไฟล์ใหญ่เกิน 25 MB")
    mime = mime_of(name, data)
    ext = {"application/pdf": ".pdf", "image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
    path = f"licences/{int(doc_id)}/{uuid.uuid4().hex}{ext}"
    supabase.storage_upload(path, data, mime, bucket=supabase.PRIVATE_BUCKET)
    got: dict = {}
    if read:
        try:
            got = read_scan(data, mime)
        except Exception as exc:                       # noqa: BLE001 -- the file is kept; the dates are typed instead
            logger.warning("licence read failed: %r", exc)
            got = {"error": "อ่านวันที่จากไฟล์ไม่สำเร็จ — ใส่เองได้"}
    supabase.update("company_doc_files", {"doc_id": f"eq.{int(doc_id)}", "current": "eq.true"}, {"current": False})
    row = supabase.insert("company_doc_files", [{"doc_id": int(doc_id), "storage_path": path, "name": (name or "")[:200], "mime": mime,
                                                 "read": got or None, "current": True, "uploaded_by": who}])[0]
    return {"file": {"id": row["id"], "name": row["name"]}, "read": got, "suggest": suggest(doc["kind"], got, doc)}


def file_data(file_id: int) -> tuple[bytes, str, str]:
    from app import supabase
    f = supabase.select("company_doc_files", {"select": "storage_path,name,mime", "id": f"eq.{int(file_id)}", "limit": "1"})
    if not f:
        raise LicenceError("ไม่พบไฟล์นี้")
    f = f[0]
    return supabase.storage_download(f["storage_path"], bucket=supabase.PRIVATE_BUCKET), f.get("mime") or "application/octet-stream", f.get("name") or "file"


def calendar_feed(now: datetime) -> str:
    return ics(_docs(), _people(), now)


def summary(today: date) -> dict:
    """For the home page: counts and the next few."""
    out = view(_docs(), [], _people(), today)
    return {"ready": True, "expired": out["tiles"]["expired"], "soon": out["tiles"]["soon"],
            "next": [{"title": r["title"], "person": r["person"], "place": r["place"], "days": r["days"], "state": r["state"],
                      "expires_on": r["expires_on"]} for r in out["act"] if r["state"] != "nodate"][:6]}
