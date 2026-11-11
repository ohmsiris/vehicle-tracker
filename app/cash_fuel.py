"""
น้ำมันเงินสด: the bookkeeper's รายการค่าน้ำมัน, done by IceBox (owner, 11 Nov 2026).

The drivers fill up with cash and write it on the handwritten รายการเบิก pages ("เติมน้ำมัน 2ฒค3332 (52)  1000  ผู้เบิก  ขับรถ"). The
bookkeeper used to pick those lines out and type the รายการค่าน้ำมัน for HQ: per truck -- รถเบอร์, ทะเบียน, พนักงานขับรถ, every fill
(date, amount), the truck's total -- and the grand total, for the company month 26 - 25. Now:

* photos of the pages are uploaded (twice a month: on the 11th the 26-10 pages, on the 26th the 11-25 ones); each photo is read once
  (its hash is kept: the same photo is refused) by Claude, which returns every line; only the fuel lines are kept;
* each line gets its truck (the number in brackets, else the plate as written, else a plate's last 4 digits), the truck's driver from
  IceBox, its company month and half (from its own date); a line equal to one already kept (same day, truck, amount) from ANOTHER
  page is marked as a duplicate and not counted -- the same fill is never counted twice;
* the bookkeeper checks the lines against the photo (truck, amount, counted or not), and prints / downloads the summary;
* the kept lines are the cash fuel for the accounts (per truck, per company month).
"""
import hashlib
import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Optional

logger = logging.getLogger("vehicle_tracker.cash_fuel")
TH_MONTHS = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


class CashFuelError(ValueError):
    """Refused, with a message shown as it is."""


# --------------------------------------------------------------------------------------------- the company month (pure)
def month_of(d: date) -> tuple[str, int]:
    """A day -> (company month, half). The company month runs 26 - 25 and is named after the month it ends in; half 1 = 26-10."""
    if d.day >= 26:
        nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        return f"{nxt.year:04d}-{nxt.month:02d}", 1
    return f"{d.year:04d}-{d.month:02d}", 1 if d.day <= 10 else 2


def period(month: str, batch: Optional[int] = None) -> tuple[date, date]:
    y, m = (int(x) for x in month.split("-"))
    end = date(y, m, 25)
    start = (date(y, m, 1) - timedelta(days=1)).replace(day=26)
    if batch == 1:
        return start, date(y, m, 10)
    if batch == 2:
        return date(y, m, 11), end
    return start, end


def be(d: date) -> str:
    return f"{d.day} {TH_MONTHS[d.month]} {d.year + 543}"


def short_name(full: Optional[str]) -> str:
    """'มานัส ใจดี' -> 'มานัส' (the summary's พนักงานขับรถ column)."""
    return (full or "").strip().split(" ")[0] if full else ""


def norm_plate(s: Optional[str]) -> str:
    return re.sub(r"[\s\-\.]", "", s or "")


def match_vehicle(plate: Optional[str], number: Optional[str], roster: list[dict], branch: str = "สระบุรี") -> Optional[dict]:
    """The truck of a fuel line: the number in brackets ((52) / 05) first -- the drivers write it reliably -- then the plate as written,
    then a plate's last 4 digits when only one truck ends that way."""
    num = re.sub(r"\D", "", number or "")
    if num:
        num = str(int(num))
        hits = [v for v in roster if str(v.get("truck_number") or "").strip() == num]
        mine = [v for v in hits if v.get("branch") == branch]
        if len(mine) == 1:
            return mine[0]
        if len(hits) == 1:
            return hits[0]
    p = norm_plate(plate)
    if p:
        for v in roster:
            if norm_plate(v.get("plate")) == p:
                return v
        digits = re.sub(r"\D", "", p)
        if len(digits) >= 4:
            hits = [v for v in roster if re.sub(r"\D", "", v.get("plate") or "").endswith(digits[-4:])]
            mine = [v for v in hits if v.get("branch") == branch]
            if len(mine) == 1:
                return mine[0]
            if len(hits) == 1:
                return hits[0]
    return None


# --------------------------------------------------------------------------------------------- reading a page (Claude)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lines": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "date": {"type": ["string", "null"], "description": "YYYY-MM-DD (Gregorian)"},
                "text": {"type": "string"},
                "is_fuel": {"type": "boolean"},
                "plate": {"type": ["string", "null"]},
                "truck_number": {"type": ["string", "null"]},
                "amount": {"type": ["number", "null"]},
                "requester": {"type": ["string", "null"]},
                "position": {"type": ["string", "null"]},
                "unsure": {"type": "boolean"},
            },
            "required": ["date", "text", "is_fuel", "plate", "truck_number", "amount", "requester", "position", "unsure"]}},
    },
    "required": ["lines"],
}

PROMPT = """You read a page of a Thai ice factory's handwritten expense list (รายการเบิก). Columns: วัน/เดือน/ปี (date), รายการ (what),
จำนวนเงิน (amount), ผู้เบิก (who took the money, a signature / name), ตำแหน่ง (their job, e.g. ขับรถ, ช่าง, ธุรการ).

Return EVERY line on the page, top to bottom, exactly as written:
- date: the date column is often written only on the first line of a day: a line with no date has the date of the line above.
  Dates are Buddhist Era, often short ("26/10/69", "1-11-69", "3/11/69"): 69 = 2569 BE = 2026; return YYYY-MM-DD Gregorian.
- text: the รายการ as written (Thai).
- is_fuel: true for buying fuel (เติมน้ำมัน, น้ำมันรถ ...), also fuel for a forklift / other vehicle; false for anything else.
- plate: the vehicle plate as written in a fuel line, e.g. "2ฒค 3332", "84-4764", "1ฒฒ 2085", "มพ 1093", "6161" (keep it as written; null if none).
- truck_number: the truck number, usually in brackets after the plate "(52)", or a number standing alone after the plate ("05");
  null if none.
- amount: the จำนวนเงิน as a number (no commas). A stray leading mark like ",1000" or "1,000" is 1000.
- requester / position: as read; null if blank.
- unsure: true if you could not read the amount, date, plate or number with confidence.
Do not invent lines; skip empty rows."""


READ_MODEL = "claude-opus-5-5"     # the handwriting is hard: the most capable Opus, high effort, and every page read twice


def read_page(data: bytes, mime: str) -> dict:
    from app.extraction import _client_lazy, _content_block
    last = None
    for attempt in (1, 2):
        try:
            r = _client_lazy().beta.messages.create(
                model=READ_MODEL, max_tokens=16000, system=[{"type": "text", "text": PROMPT}],
                messages=[{"role": "user", "content": [_content_block(data, mime), {"type": "text", "text": "Read every line of this page."}]}],
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")       # a declined page is retried on another model
            if r.stop_reason == "refusal":
                raise RuntimeError("the model declined this page")
            out = json.loads(next(b.text for b in r.content if b.type == "text"))
            if not isinstance(out.get("lines"), list):
                raise ValueError("no lines")
            return out
        except Exception as exc:                       # noqa: BLE001 -- retried once, then reported on the page
            last = exc
            logger.warning("cash fuel read attempt %d failed: %r", attempt, exc)
    raise RuntimeError(f"อ่านหน้านี้ไม่สำเร็จ ({last})")


def read_twice(data: bytes, mime: str) -> dict:
    """The page read twice, independently (owner, 11 Nov 2026: the handwriting is very hard). The first read is kept; the second is
    compared with it line by line."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(lambda _: read_page(data, mime), (1, 2))
    return {"lines": a.get("lines") or [], "second": b.get("lines") or []}


def _same(x, y, key: str) -> bool:
    if key == "amount":
        return abs(float(x or 0) - float(y or 0)) < 0.5
    if key in ("plate", "truck_number"):
        return re.sub(r"[^\w]", "", str(x or "")) == re.sub(r"[^\w]", "", str(y or ""))
    return (x or None) == (y or None)


def disagreements(first: list[dict], second: list[dict]) -> dict:
    """{index of a fuel line in `first`: what the second read saw differently}. Fuel lines are matched in order."""
    fa = [i for i, ln in enumerate(first) if ln.get("is_fuel")]
    fb = [ln for ln in second if ln.get("is_fuel")]
    out = {}
    names = {"date": "วันที่", "amount": "จำนวนเงิน", "plate": "ทะเบียน", "truck_number": "เบอร์รถ"}
    for k, i in enumerate(fa):
        if k >= len(fb):
            out[i] = "รอบสองไม่เห็นบรรทัดนี้"
            continue
        diff = [f"{names[key]} {fb[k].get(key) or '—'}" for key in names if not _same(first[i].get(key), fb[k].get(key), key)]
        if diff:
            out[i] = "อ่านรอบสองได้ " + ", ".join(diff)
    if len(fb) > len(fa) and fa:
        out[fa[-1]] = " · ".join(x for x in (out.get(fa[-1]), f"รอบสองเห็นบรรทัดน้ำมัน {len(fb)} บรรทัด รอบแรก {len(fa)}") if x)
    return out


def fuel_lines(read: dict, roster: list[dict], branch: str, ppl: Optional[dict] = None) -> list[dict]:
    """The fuel lines of a read page, with their truck, driver, company month and half; where the two reads disagree, the line says
    what the second read saw (the bookkeeper checks it against the photo)."""
    out = []
    second = disagreements(read.get("lines") or [], read.get("second") or []) if read.get("second") is not None else {}
    for i, ln in enumerate(read.get("lines") or []):
        if not ln.get("is_fuel"):
            continue
        try:
            d = date.fromisoformat(str(ln.get("date") or ""))
        except ValueError:
            d = None
        v = match_vehicle(ln.get("plate"), ln.get("truck_number"), roster, branch)
        notes = []
        by_plate = match_vehicle(ln.get("plate"), None, roster, branch) if ln.get("plate") and ln.get("truck_number") else None
        if v and by_plate and by_plate["id"] != v["id"]:
            notes.append(f"ทะเบียน {ln.get('plate')} เป็นของรถ {by_plate.get('truck_number') or by_plate.get('plate')} แต่เขียนเบอร์ {ln.get('truck_number')}")
        if not d:
            notes.append("อ่านวันที่ไม่ได้")
        took = who_took(ln.get("requester"), ppl or {})
        if not v and took:                 # no plate / number written: the truck คนเบิก drives in IceBox (owner, 11 Nov 2026), to be checked
            v = next((x for x in roster if (x.get("driver") or "").strip() == took.strip() and (not x.get("branch") or x.get("branch") == branch)), None)
            if v:
                notes.append(f"ไม่ได้เขียนทะเบียน — ใช้รถที่ {short_name(took)} ขับ")
        if not v:
            notes.append("ไม่พบรถ")
        if ln.get("unsure"):
            notes.append("อ่านไม่ชัด — ตรวจกับรูป")
        if i in second:
            notes.append(second[i])
        out.append({"seq": i, "day": d, "text": (ln.get("text") or "")[:200], "plate_read": ln.get("plate"), "number_read": ln.get("truck_number"),
                    "counted": not is_foh(took, ppl or {}),
                    "amount": float(ln.get("amount") or 0), "requester": ln.get("requester"), "position": ln.get("position"),
                    "vehicle_id": v["id"] if v else None,
                    "driver_name": took or ((v.get("driver") or None) if v else None),
                    "note": " · ".join(notes) or None})
    return out


# --------------------------------------------------------------------------------------------- who took the money (คนเบิก)
GROUPS = (("route", "พนักงานขับรถสาย", "คนขับรถสาย"), ("foh", "พนักงานขับรถหน้าโรง", "หน้าโรง"))


def people(branch: str, roster: list[dict]) -> dict:
    """The คนเบิก dropdown: {"route": [...], "foh": [...]} of active drivers (full name, nickname, their route truck's number)."""
    from app import supabase
    truck_of = {(v.get("driver") or "").strip(): v.get("truck_number") for v in roster if v.get("driver") and v.get("branch") == branch}
    out = {k: [] for k, _, _ in GROUPS}
    pos = {p: k for k, p, _ in GROUPS}
    for e in supabase.select_all("employees", {"select": "full_name,nickname,position", "branch": f"eq.{branch}", "status": "eq.active",
                                               "position": f"in.({','.join(p for _, p, _ in GROUPS)})", "order": "full_name.asc"}):
        out[pos[e["position"]]].append({"name": e["full_name"], "nickname": e.get("nickname") or "", "truck": truck_of.get((e["full_name"] or "").strip())})
    out["route"].sort(key=lambda x: (int(x["truck"]) if str(x["truck"] or "").isdigit() else 999, x["name"]))
    return out


def is_foh(name: Optional[str], ppl: dict) -> bool:
    """คนเบิก is a หน้าโรง driver: the fill is kept for the accounts but is not on HQ's รายการค่าน้ำมัน (owner, 11 Nov 2026)."""
    return bool(name) and any(p["name"] == name for p in (ppl or {}).get("foh") or [])


def who_took(requester: Optional[str], ppl: dict) -> Optional[str]:
    """The ผู้เบิก as read ('มานัส') -> a driver's full name, when it is one driver's first name or nickname."""
    r = re.sub(r"\s+", "", requester or "")
    if len(r) < 2:
        return None
    hits = [p["name"] for k in ppl for p in ppl[k]
            if r in (re.sub(r"\s+", "", p["nickname"] or ""), re.sub(r"\s+", "", (p["name"] or "").split(" ")[0]))]
    return hits[0] if len(set(hits)) == 1 else None


# --------------------------------------------------------------------------------------------- storing
def _roster() -> list[dict]:
    from app.roster import load_roster
    return load_roster()


MAX_PDF_PAGES = 30


def pdf_pages(raw: bytes) -> list[bytes]:
    """A PDF (a scan of several รายการเบิก pages) -> one JPEG per page, so each page is read, checked and confirmed on its own
    (owner, 11 Nov 2026: a PDF of many pages did not work)."""
    import pymupdf
    doc = pymupdf.open(stream=raw, filetype="pdf")
    if doc.page_count > MAX_PDF_PAGES:
        raise CashFuelError(f"PDF มี {doc.page_count} หน้า — ได้ไม่เกิน {MAX_PDF_PAGES} หน้าต่อไฟล์")
    out = []
    for page in doc:
        zoom = min(2000 / max(page.rect.width, page.rect.height, 1), 200 / 72)       # about 2000 px on the long side
        out.append(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB).tobytes("jpeg", jpg_quality=85))
    return out


def add_pages(files: list[tuple[str, bytes]], branch: str, by_name: str) -> dict:
    """Photos (or PDFs, split into their pages) of รายการเบิก pages -> read, fuel lines kept. A page already uploaded is refused
    (never read twice)."""
    import mimetypes

    from app import customers, supabase
    roster = _roster()
    ppl = people(branch, roster)
    todo, refused = [], []
    for name, raw in files:
        mime = mimetypes.guess_type(name)[0] or "image/jpeg"
        if mime not in ("image/jpeg", "image/png", "image/webp", "application/pdf"):
            refused.append(f"{name}: รับเฉพาะรูป หรือ PDF")
            continue
        if mime == "application/pdf":
            try:
                pages = pdf_pages(raw)
            except CashFuelError as exc:
                refused.append(f"{name}: {exc}")
                continue
            except Exception:                          # noqa: BLE001
                refused.append(f"{name}: เปิด PDF นี้ไม่ได้")
                continue
            whole = hashlib.sha256(raw).hexdigest()
            items = [(f"{name} หน้า {i + 1}" if len(pages) > 1 else name, hashlib.sha256(f"{whole}:{i}".encode()).hexdigest(), jpg)
                     for i, jpg in enumerate(pages)]
        else:
            items = [(name, hashlib.sha256(raw).hexdigest(), raw)]
        for nm, h, data in items:
            if supabase.select("cash_fuel_pages", {"select": "id", "image_hash": f"eq.{h}", "limit": "1"}):
                refused.append(f"{nm}: อัปโหลดรูปนี้ไปแล้ว — ไม่อ่านซ้ำ")
                continue
            data, out_mime, ext = customers.shrink(data, "image/jpeg" if mime == "application/pdf" else mime)
            todo.append((nm, h, data, out_mime, ext))

    def work(item):
        name, h, data, out_mime, ext = item
        try:
            return item, read_twice(data, out_mime), None
        except Exception as exc:                       # noqa: BLE001
            return item, None, str(exc)

    added, lines_n = [], 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(work, todo))
    for (name, h, data, out_mime, ext), read, err in results:
        path = f"cashfuel/{h[:2]}/{h}.{ext}"
        supabase.storage_upload(path, data, out_mime, upsert=True, bucket=supabase.PRIVATE_BUCKET)
        page = supabase.insert("cash_fuel_pages", [{"branch": branch, "storage_path": path, "image_hash": h, "name": name[:120], "by_name": by_name,
                                                    "status": "error" if err else "read", "error": err, "raw": read}])[0]
        rows = []
        for ln in fuel_lines(read or {}, roster, branch, ppl):
            if not ln["day"]:
                ln["day"] = date.today()
            m, b = month_of(ln["day"])
            rows.append({**ln, "day": ln["day"].isoformat(), "page_id": page["id"], "branch": branch, "month": m, "batch": b})
        _mark_duplicates(rows, branch)
        if rows:
            supabase.insert("cash_fuel_lines", rows)
        added.append({"page_id": page["id"], "name": name, "lines": len(rows), "error": err})
        lines_n += len(rows)
    return {"added": added, "refused": refused, "lines": lines_n}


def _mark_duplicates(rows: list[dict], branch: str) -> None:
    """A fill already counted from another page (same day, truck, amount) is not counted again."""
    from app import supabase
    for r in rows:
        if not r.get("vehicle_id"):
            continue
        same = supabase.select("cash_fuel_lines", {"select": "id", "branch": f"eq.{branch}", "day": f"eq.{r['day']}", "vehicle_id": f"eq.{r['vehicle_id']}",
                                                   "amount": f"eq.{r['amount']}", "counted": "eq.true", "limit": "1"})
        if same:
            r["counted"], r["dup_of"] = False, same[0]["id"]
            r["note"] = " · ".join(x for x in (r.get("note"), "ซ้ำกับรายการที่นับแล้ว — ไม่นับซ้ำ") if x)


def _confirmed_lines(lines: list[dict]) -> list[dict]:
    """Only what someone has checked against the photo counts (owner, 11 Nov 2026): lines of confirmed pages, and lines typed in."""
    from app import supabase
    ids = sorted({ln["page_id"] for ln in lines if ln.get("page_id")})
    ok = {p["id"] for p in supabase.select_all("cash_fuel_pages", {"select": "id", "id": f"in.({','.join(map(str, ids))})", "confirmed": "eq.true",
                                                                   "order": "id.asc"})} if ids else set()
    return [ln for ln in lines if not ln.get("page_id") or ln["page_id"] in ok]


def _choices(branch: str) -> dict:
    full = _roster()
    roster = [{"id": v["id"], "truck_number": v.get("truck_number"), "plate": v.get("plate"), "branch": v.get("branch"), "usage": v.get("usage"),
               "driver": v.get("driver")} for v in full]
    return {"vehicles": roster, "people": people(branch, full), "groups": [{"key": k, "label": lab} for k, _, lab in GROUPS]}


def month_view(branch: str, month: str) -> dict:
    """One company month: its two periods (by their dates), the confirmed fills and the summary of each; pages still to check."""
    from app import supabase
    lines = _confirmed_lines(supabase.select_all("cash_fuel_lines", {"select": "*", "branch": f"eq.{branch}", "month": f"eq.{month}",
                                                                     "order": "day.asc,page_id.asc,seq.asc"}))
    ch = _choices(branch)
    periods = []
    for b in (1, 2):
        s, e = period(month, b)
        sm = summary(lines, ch["vehicles"], b)
        periods.append({"batch": b, "from": s.isoformat(), "to": e.isoformat(), "total": sm["total"], "fills": sm["fills"], "trucks": len(sm["trucks"])})
    s, e = period(month)
    return {"branch": branch, "month": month, "from": s.isoformat(), "to": e.isoformat(), "lines": lines, "periods": periods,
            "pending": pending(branch), **ch}


def pending(branch: str) -> list[dict]:
    """Pages read but not confirmed yet (any month): what is waiting to be checked, oldest first."""
    from app import supabase
    pages = supabase.select_all("cash_fuel_pages", {"select": "id,name,status,error,created_at", "branch": f"eq.{branch}", "confirmed": "eq.false",
                                                    "order": "id.asc"})
    if not pages:
        return []
    lines = supabase.select_all("cash_fuel_lines", {"select": "page_id,day,amount,note,checked,counted",
                                                    "page_id": f"in.({','.join(str(p['id']) for p in pages)})", "order": "id.asc"})
    for p in pages:
        mine = [ln for ln in lines if ln["page_id"] == p["id"]]
        days = sorted(ln["day"] for ln in mine)
        p.update({"lines": len(mine), "to_check": sum(1 for ln in mine if ln.get("note") and not ln.get("checked")),
                  "from": days[0] if days else None, "to": days[-1] if days else None,
                  "total": round(sum(float(ln["amount"] or 0) for ln in mine if ln.get("counted")), 2)})
    return pages


def page_detail(page_id: int, branch: str = "สระบุรี") -> dict:
    """One page to check: the page, its fuel lines (the doubtful ones first), and the choices for truck / คนเบิก."""
    from app import supabase
    p = supabase.select("cash_fuel_pages", {"select": "id,name,status,error,confirmed,confirmed_at,confirmed_by,created_at,branch",
                                            "id": f"eq.{int(page_id)}", "limit": "1"})
    if not p:
        raise CashFuelError("ไม่พบรูปนี้")
    lines = supabase.select_all("cash_fuel_lines", {"select": "*", "page_id": f"eq.{int(page_id)}", "order": "seq.asc"})
    queue = [x["id"] for x in pending(p[0]["branch"])]
    return {"page": p[0], "lines": lines, "queue": queue, **_choices(p[0]["branch"])}


def confirm_page(page_id: int, who: str, confirmed: bool = True) -> dict:
    """ยืนยันหน้านี้: the page has been checked against the photo; its lines now count. Every doubtful line must be ticked first."""
    from app import supabase
    lines = supabase.select_all("cash_fuel_lines", {"select": "id,note,checked,counted,vehicle_id,amount", "page_id": f"eq.{int(page_id)}", "order": "id.asc"})
    if confirmed:
        open_ = [ln for ln in lines if ln.get("note") and not ln.get("checked") and ln.get("counted")]
        if open_:
            raise CashFuelError(f"ยังมี {len(open_)} รายการที่ต้องดูกับรูป — กด “ถูกแล้ว” หรือแก้ / ลบแถว ก่อนยืนยัน")
        empty = [ln for ln in lines if ln.get("counted") and (not ln.get("vehicle_id") or float(ln.get("amount") or 0) <= 0)]
        if empty:
            raise CashFuelError(f"ยังมี {len(empty)} แถวที่ไม่มีรถหรือยอด — ใส่ให้ครบ หรือลบแถว ก่อนยืนยัน")
        supabase.update("cash_fuel_lines", {"page_id": f"eq.{int(page_id)}"}, {"checked": True})
    now = datetime.now(timezone.utc).isoformat()
    supabase.update("cash_fuel_pages", {"id": f"eq.{int(page_id)}"},
                    {"confirmed": confirmed, "confirmed_at": now if confirmed else None, "confirmed_by": who if confirmed else None})
    return {"ok": True}


def recent_pages(branch: str, limit: int = 30) -> list[dict]:
    from app import supabase
    return supabase.select("cash_fuel_pages", {"select": "id,name,status,error,created_at", "branch": f"eq.{branch}", "order": "id.desc", "limit": str(limit)})


def update_line(line_id: int, body: dict) -> dict:
    from app import supabase
    values = {}
    if "day" in body:
        try:
            d = date.fromisoformat(str(body["day"]))
        except ValueError:
            raise CashFuelError("วันที่ไม่ถูกต้อง")
        m, b = month_of(d)
        values.update({"day": d.isoformat(), "month": m, "batch": b})
    if "vehicle_id" in body:
        values["vehicle_id"] = body["vehicle_id"] or None
        if body["vehicle_id"] and "driver_name" not in body:
            v = next((x for x in _roster() if x["id"] == body["vehicle_id"]), None)
            values["driver_name"] = (v.get("driver") or None) if v else None
    for k in ("driver_name", "note"):
        if k in body:
            values[k] = (str(body[k] or "").strip()[:100]) or None
    if "amount" in body:
        try:
            values["amount"] = round(float(str(body["amount"]).replace(",", "")), 2)
        except ValueError:
            raise CashFuelError("จำนวนเงินไม่ถูกต้อง")
    for k in ("counted", "checked"):
        if k in body:
            values[k] = bool(body[k])
    if not values:
        raise CashFuelError("ไม่มีอะไรเปลี่ยน")
    if "driver_name" in values and "counted" not in body:
        cur = supabase.select("cash_fuel_lines", {"select": "branch,dup_of", "id": f"eq.{int(line_id)}", "limit": "1"})
        if cur:
            values["counted"] = not cur[0].get("dup_of") and not is_foh(values["driver_name"], people(cur[0]["branch"], _roster()))
    rows = supabase.update("cash_fuel_lines", {"id": f"eq.{int(line_id)}"}, values)
    if not rows:
        raise CashFuelError("ไม่พบรายการนี้")
    return rows[0]


def add_line(branch: str, body: dict) -> dict:
    """A fill the reader missed, typed in by the bookkeeper."""
    from app import supabase
    try:
        d = date.fromisoformat(str(body.get("day")))
        amount = round(float(str(body.get("amount") or 0).replace(",", "")), 2)
    except ValueError:
        raise CashFuelError("ใส่วันที่และจำนวนเงินให้ถูกต้อง")
    roster = _roster()
    v = next((x for x in roster if x["id"] == body.get("vehicle_id")), None)
    m, b = month_of(d)
    page_id, seq = body.get("page_id"), 999
    if page_id:                                     # เพิ่มแถว under a page's table: the line belongs to that page, after its last row
        page_id = int(page_id)
        have = supabase.select_all("cash_fuel_lines", {"select": "seq", "page_id": f"eq.{page_id}", "order": "seq.asc"})
        seq = max([int(x["seq"] or 0) for x in have] + [-1]) + 1
    name = body.get("driver_name") or ((v.get("driver") or None) if v else None)
    return supabase.insert("cash_fuel_lines", [{"page_id": page_id or None, "branch": branch, "month": m, "batch": b, "day": d.isoformat(), "text": "เพิ่มเอง",
                                                "amount": amount, "vehicle_id": v["id"] if v else None, "driver_name": name,
                                                "counted": not is_foh(name, people(branch, roster)), "checked": True, "seq": seq}])[0]


def delete_line(line_id: int) -> dict:
    from app import supabase
    supabase.delete("cash_fuel_lines", {"id": f"eq.{int(line_id)}"})
    return {"ok": True}


def delete_page(page_id: int) -> dict:
    """A photo uploaded by mistake: the page, its lines and the stored photo go."""
    from app import supabase
    p = supabase.select("cash_fuel_pages", {"select": "id,storage_path", "id": f"eq.{int(page_id)}", "limit": "1"})
    if not p:
        raise CashFuelError("ไม่พบรูปนี้")
    supabase.delete("cash_fuel_pages", {"id": f"eq.{int(page_id)}"})
    supabase.storage_delete([p[0]["storage_path"]], bucket=supabase.PRIVATE_BUCKET)
    return {"ok": True}


def page_image(page_id: int) -> tuple[bytes, str]:
    from app import supabase
    p = supabase.select("cash_fuel_pages", {"select": "storage_path", "id": f"eq.{int(page_id)}", "limit": "1"})
    if not p:
        raise CashFuelError("ไม่พบรูปนี้")
    path = p[0]["storage_path"]
    return supabase.storage_download(path, bucket=supabase.PRIVATE_BUCKET), "application/pdf" if path.endswith(".pdf") else "image/jpeg"


# --------------------------------------------------------------------------------------------- the summary (pure)
def _truck_key(v: Optional[dict]):
    n = str((v or {}).get("truck_number") or "")
    return (0, int(n), "") if n.isdigit() else (1, 0, n) if v else (2, 0, "")


def summary(lines: list[dict], roster: list[dict], batch: Optional[int] = None) -> dict:
    """รายการค่าน้ำมัน: per truck (by its number) its plate, driver, every counted fill and its total; the grand total."""
    by_id = {v["id"]: v for v in roster}
    groups: dict = {}
    for ln in lines:
        if not ln.get("counted") or (batch and ln.get("batch") != batch):
            continue
        key = ln.get("vehicle_id") or "?"
        g = groups.setdefault(key, {"vehicle": by_id.get(ln.get("vehicle_id")), "fills": [], "drivers": Counter()})
        g["fills"].append({"day": ln["day"], "amount": float(ln.get("amount") or 0)})
        if ln.get("driver_name"):
            g["drivers"][short_name(ln["driver_name"])] += 1           # the summary writes first names, as the bookkeeper did
    trucks = []
    for key, g in sorted(groups.items(), key=lambda kv: _truck_key(kv[1]["vehicle"])):
        v = g["vehicle"] or {}
        fills = sorted(g["fills"], key=lambda f: f["day"])
        trucks.append({"truck_no": v.get("truck_number") or "", "plate": v.get("plate") or "ไม่ทราบรถ", "driver": ", ".join(n for n, _ in g["drivers"].most_common(2)),
                       "fills": fills, "total": round(sum(f["amount"] for f in fills), 2)})
    rows = []                                                           # by date (owner, 11 Nov: HQ gets both lists)
    for ln in lines:
        if not ln.get("counted") or (batch and ln.get("batch") != batch):
            continue
        v = by_id.get(ln.get("vehicle_id")) or {}
        rows.append({"day": ln["day"], "truck_no": v.get("truck_number") or "", "plate": v.get("plate") or "ไม่ทราบรถ",
                     "driver": short_name(ln["driver_name"]) if ln.get("driver_name") else "", "amount": float(ln.get("amount") or 0),
                     "_k": (ln["day"], _truck_key(v or None))})
    rows.sort(key=lambda x: x.pop("_k"))
    return {"trucks": trucks, "by_date": rows, "total": round(sum(t["total"] for t in trucks), 2), "fills": sum(len(t["fills"]) for t in trucks)}


def to_xlsx(month: str, s: dict, batch: Optional[int] = None) -> bytes:
    """The รายการค่าน้ำมัน as the bookkeeper's, two sheets: by date (one row per fill, in date order) and by truck (number, plate
    and driver over all its fills, its total). Grand total at the end of each. TH Sarabun New, A4, black and white."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    start, end = period(month, batch)
    F = "TH Sarabun New"
    thin = Side(style="thin", color="000000")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    c, l = Alignment(horizontal="center", vertical="center", wrap_text=True), Alignment(horizontal="left", vertical="center")
    when = f"วันที่ {start.day} {TH_MONTHS[start.month]} {start.year + 543}  ถึงวันที่ {end.day} {TH_MONTHS[end.month]} {end.year + 543}"
    th_day = lambda iso: (lambda d: f"{d.day}/{d.month}/{d.year + 543}")(date.fromisoformat(iso))

    def head(ws, title, heads, widths):
        ws.title = title
        last = "ABCDEF"[len(heads) - 1]
        for col, w in zip("ABCDEF", widths):
            ws.column_dimensions[col].width = w
        ws.merge_cells(f"A1:{last}1")
        ws["A1"], ws["A1"].font, ws["A1"].alignment = "รายการค่าน้ำมัน", Font(name=F, size=20, bold=True), c
        ws.merge_cells(f"A2:{last}2")
        ws["A2"], ws["A2"].font, ws["A2"].alignment = when, Font(name=F, size=15), l
        for i, h in enumerate(heads, start=1):
            cell = ws.cell(row=4, column=i, value=h)
            cell.font, cell.alignment, cell.border = Font(name=F, size=15, bold=True), c, box

    def put(ws, r, col, val, size=14, bold=False, money=False):
        cell = ws.cell(row=r, column=col, value=val)
        cell.font, cell.alignment, cell.border = Font(name=F, size=size, bold=bold), c, box
        ws.row_dimensions[r].height = 22
        if money:
            cell.number_format = "#,##0.##"
        return cell

    def foot(ws, r, ncols):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols - 1)
        for col in range(1, ncols + 1):
            ws.cell(row=r, column=col).border = box
        put(ws, r, 1, "รวม", 15, True)
        put(ws, r, ncols, s["total"], 15, True, True)
        ws.print_title_rows = "4:4"
        ws.page_setup.paperSize, ws.page_setup.orientation = ws.PAPERSIZE_A4, "portrait"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.oddFooter.center.text = "หน้า &P / &N"

    wb = Workbook()
    # 1. by date: ลำดับ วันที่ เบอร์รถ ทะเบียนรถ พนักงานขับรถ จำนวน
    ws = wb.active
    head(ws, "ตามวันที่", ("ลำดับ", "วันที่", "เบอร์รถ", "ทะเบียนรถ", "พนักงานขับรถ", "จำนวน"), (8, 15, 11, 17, 20, 13))
    r = 5
    for i, x in enumerate(s.get("by_date") or [], start=1):
        for col, val in enumerate((i, th_day(x["day"]), x["truck_no"], x["plate"], x["driver"], x["amount"]), start=1):
            put(ws, r, col, val, money=col == 6)
        r += 1
    foot(ws, r, 6)

    # 2. by truck: รถเบอร์ ทะเบียนรถ พนักงานขับรถ วัน/เดือน/ปี จำนวน รวม
    ws = wb.create_sheet()
    head(ws, "ตามรถ", ("รถเบอร์", "ทะเบียนรถ", "พนักงานขับรถ", "วัน/เดือน/ปี", "จำนวน", "รวม"), (9, 16, 21, 18, 12, 13))
    r = 5
    for t in s["trucks"]:
        n = max(1, len(t["fills"]))
        for j, f in enumerate(t["fills"]):
            put(ws, r + j, 4, th_day(f["day"]), bold=True)
            put(ws, r + j, 5, f["amount"], bold=True, money=True)
        for col, val in ((1, t["truck_no"]), (2, t["plate"]), (3, t["driver"]), (6, t["total"])):
            if n > 1:
                ws.merge_cells(start_row=r, start_column=col, end_row=r + n - 1, end_column=col)
            put(ws, r, col, val, 15, col in (1, 6), col == 6)
            for k in range(n):
                ws.cell(row=r + k, column=col).border = box
        r += n
    foot(ws, r, 6)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def summary_file(branch: str, month: str, batch: Optional[int], kind: str) -> tuple[bytes, str, str]:
    from app import supabase
    lines = _confirmed_lines(supabase.select_all("cash_fuel_lines", {"select": "*", "branch": f"eq.{branch}", "month": f"eq.{month}", "order": "day.asc"}))
    roster = [{"id": v["id"], "truck_number": v.get("truck_number"), "plate": v.get("plate")} for v in _roster()]
    s = summary(lines, roster, batch)
    data = to_xlsx(month, s, batch)
    start, end = period(month, batch)
    name = f"รายการค่าน้ำมัน {branch} {start.day}-{start.month}-{start.year + 543} ถึง {end.day}-{end.month}-{end.year + 543}"
    if kind == "pdf":
        from app import route_docx
        data = route_docx.xlsx_to_pdf(data)
        return data, "application/pdf", name + ".pdf"
    return data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", name + ".xlsx"
