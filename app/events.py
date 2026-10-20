"""
Saves one confirmed maintenance event: validates what the review page sent,
uploads its files to Supabase Storage, then writes the event, its
attachments and its line items in ONE database call (the
save_maintenance_event() function in schema_v2.sql), so there are never
half-saved events. If anything fails after files were uploaded, the uploads
are deleted again.

Contract with the page (see static/app.js buildPayload): `attachments` is
ordered bills first, then reference photos, and each line item's
`file_index` is an index into that same list (or null for a line typed in
by hand with no bill behind it).
"""
import hashlib
import logging
import uuid
from datetime import date, timedelta
from typing import Optional

from app import supabase
from app.extraction import CATEGORIES
from app.files import UnsupportedFile, prepare_file

logger = logging.getLogger("vehicle_tracker.events")

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}
SOURCES = ("bill", "manual")
ATTACHMENT_TYPES = ("bill", "reference_photo")


class InvalidEvent(ValueError):
    """Something the user can fix. str() is a Thai message safe to show."""


class DuplicateBill(Exception):
    """A bill file in this batch was already saved before."""

    def __init__(self, duplicates: list[dict]):
        super().__init__("duplicate bill file")
        self.duplicates = duplicates   # [{"filename", "event_id", "saved_at"}]


def _parse_date(raw) -> date:
    try:
        d = date.fromisoformat(str(raw))
    except ValueError:
        raise InvalidEvent("วันที่ไม่ถูกต้อง")
    if d > date.today() + timedelta(days=1):
        raise InvalidEvent("วันที่เป็นวันในอนาคต")
    return d


def _num(raw, what: str, *, integer: bool = False) -> Optional[float]:
    if raw in (None, ""):
        return None
    try:
        n = float(raw)
    except (TypeError, ValueError):
        raise InvalidEvent(f"{what} ไม่ถูกต้อง")
    if n < 0:
        raise InvalidEvent(f"{what} ติดลบไม่ได้")
    return int(n) if integer else n


def _validate(payload: dict, n_files: int) -> None:
    try:
        uuid.UUID(str(payload.get("vehicle_id")))
    except ValueError:
        raise InvalidEvent("ยังไม่ได้เลือกรถ")
    if payload.get("source") not in SOURCES:
        raise InvalidEvent("ประเภทรายการไม่ถูกต้อง")
    ev = payload.get("event") or {}
    _parse_date(ev.get("event_date"))
    _num(ev.get("mileage"), "เลขไมล์", integer=True)
    _num(ev.get("next_service_mileage"), "ไมล์ครั้งต่อไป", integer=True)
    _num(ev.get("total_cost"), "ยอดรวม")

    fixes = payload.get("fixes_request_ids") or []
    if not isinstance(fixes, list):
        raise InvalidEvent("รายการงานซ่อมที่ปิดไม่ถูกต้อง")
    for rid in fixes:
        try:
            uuid.UUID(str(rid))
        except ValueError:
            raise InvalidEvent("รายการงานซ่อมที่ปิดไม่ถูกต้อง")

    atts = payload.get("attachments") or []
    if len(atts) != n_files:
        raise InvalidEvent("จำนวนไฟล์ไม่ตรงกับรายการที่ส่งมา")
    for a in atts:
        if a.get("type") not in ATTACHMENT_TYPES:
            raise InvalidEvent("ประเภทไฟล์ไม่ถูกต้อง")
    if payload["source"] == "bill" and not any(a["type"] == "bill" for a in atts):
        raise InvalidEvent("ต้องมีไฟล์บิลอย่างน้อย 1 ไฟล์")

    lines = payload.get("line_items") or []
    if not lines:
        raise InvalidEvent("ต้องมีรายการอย่างน้อย 1 รายการ")
    for ln in lines:
        if not (ln.get("description") or "").strip():
            raise InvalidEvent("มีรายการที่ยังไม่ได้ใส่ชื่อ")
        if ln.get("category") and ln["category"] not in CATEGORIES:
            raise InvalidEvent(f"หมวด '{ln['category']}' ไม่ถูกต้อง")
        fi = ln.get("file_index")
        if fi is not None and not (isinstance(fi, int) and 0 <= fi < len(atts) and atts[fi]["type"] == "bill"):
            raise InvalidEvent("รายการอ้างอิงไฟล์บิลที่ไม่มีอยู่")
        _num(ln.get("quantity"), "จำนวน")
        _num(ln.get("unit_cost"), "ราคา/หน่วย")
        _num(ln.get("line_total"), "ราคารวมของรายการ")


def save_event(payload: dict, files: list[tuple[str, bytes]], *, allow_duplicates: bool = False) -> dict:
    """
    payload: {vehicle_id, source, event{...}, line_items[...], attachments[{type, filename}],
              bill_extractions{"<attachment index>": {...}}, fixes_request_ids[...]}
    files:   [(filename, raw_bytes)] in the same order as payload["attachments"].
    Returns {"event_id", "attachments": [{"id", "type", "url"}]}.
    """
    _validate(payload, len(files))

    if not supabase.select("vehicles", {"select": "id", "id": f"eq.{payload['vehicle_id']}", "limit": "1"}):
        raise InvalidEvent("ไม่พบรถคันนี้ในระบบ")

    fixes = payload.get("fixes_request_ids") or []
    if fixes:
        mine = supabase.select("repair_requests", {"select": "id", "vehicle_id": f"eq.{payload['vehicle_id']}", "id": f"in.({','.join(fixes)})"})
        if len(mine) != len(set(fixes)):
            raise InvalidEvent("มีงานซ่อมที่เลือกปิด ที่ไม่ใช่ของรถคันนี้")

    # Normalize every file (type check, resize/rotate if needed) and hash the
    # bytes that will actually be stored.
    event_id = str(uuid.uuid4())
    prepared = []
    for i, (name, raw) in enumerate(files):
        try:
            data, media_type = prepare_file(raw)
        except UnsupportedFile as exc:
            raise InvalidEvent(f"{name}: {exc}")
        att_id = str(uuid.uuid4())
        prepared.append({
            "id": att_id, "type": payload["attachments"][i]["type"], "filename": name,
            "data": data, "mime_type": media_type, "sha256": hashlib.sha256(data).hexdigest(),
            "path": f"{event_id}/{att_id}.{EXTENSIONS[media_type]}", "sort_order": i,
        })

    # The same file picked twice in one batch (a double-tap in the file
    # picker) would hit the database's unique constraint -- say so plainly.
    seen: dict[str, str] = {}
    for p in prepared:
        if p["sha256"] in seen:
            raise InvalidEvent(f"ไฟล์ {p['filename']} ซ้ำกับ {seen[p['sha256']]} ในชุดเดียวกัน")
        seen[p["sha256"]] = p["filename"]

    # The same bill saved twice is the likeliest data-entry mistake: catch it.
    bills = [p for p in prepared if p["type"] == "bill"]
    if bills and not allow_duplicates:
        hashes = ",".join(p["sha256"] for p in bills)
        found = supabase.select("event_attachments", {"select": "event_id,sha256,created_at", "sha256": f"in.({hashes})"})
        by_hash = {f["sha256"]: f for f in found}
        duplicates = [
            {"filename": p["filename"], "event_id": by_hash[p["sha256"]]["event_id"], "saved_at": by_hash[p["sha256"]]["created_at"]}
            for p in bills if p["sha256"] in by_hash
        ]
        if duplicates:
            raise DuplicateBill(duplicates)

    extractions = payload.get("bill_extractions") or {}
    uploaded: list[str] = []
    try:
        for p in prepared:
            supabase.storage_upload(p["path"], p["data"], p["mime_type"])
            uploaded.append(p["path"])

        ev = payload["event"]
        supabase.rpc("save_maintenance_event", {"p": {
            "event_id": event_id,
            "vehicle_id": payload["vehicle_id"],
            "source": payload["source"],
            "event_date": ev["event_date"],
            "mileage": ev.get("mileage"),
            "next_service_mileage": ev.get("next_service_mileage"),
            "vendor": ev.get("vendor"),
            "performed_by": ev.get("performed_by"),
            "branch": ev.get("branch"),
            "total_cost": ev.get("total_cost"),
            "notes": ev.get("notes"),
            "low_confidence_fields": ev.get("low_confidence_fields") or [],
            "extraction_model": payload.get("extraction_model"),
            "attachments": [
                {
                    "id": p["id"], "type": p["type"], "storage_path": p["path"], "mime_type": p["mime_type"],
                    "byte_size": len(p["data"]), "sha256": p["sha256"], "sort_order": p["sort_order"],
                    "extraction": extractions.get(str(p["sort_order"])),
                }
                for p in prepared
            ],
            "line_items": [
                {
                    "attachment_id": prepared[ln["file_index"]]["id"] if ln.get("file_index") is not None else None,
                    "line_number": i + 1,
                    "description": ln["description"].strip(),
                    "category": ln.get("category"),
                    "quantity": ln.get("quantity"),
                    "unit": ln.get("unit"),
                    "unit_cost": ln.get("unit_cost"),
                    "line_total": ln.get("line_total"),
                    "low_confidence_fields": ln.get("low_confidence_fields") or [],
                }
                for i, ln in enumerate(payload["line_items"])
            ],
            "fixes_request_ids": payload.get("fixes_request_ids") or [],
        }})
    except Exception:
        supabase.storage_delete(uploaded)   # don't leave orphaned photos behind
        raise

    return {
        "event_id": event_id,
        "attachments": [{"id": p["id"], "type": p["type"], "url": supabase.public_url(p["path"])} for p in prepared],
    }
