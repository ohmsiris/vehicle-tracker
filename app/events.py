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

from app import history, supabase
from app.extraction import CATEGORIES
from app.files import UnsupportedFile, prepare_file

logger = logging.getLogger("vehicle_tracker.events")

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}
SOURCES = ("bill", "manual")
BUDDHIST_YEAR_FLOOR = 2400      # no real repair is dated in the 25th century; a year this high is a พ.ศ. typed as ค.ศ.
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
    if d.year >= BUDDHIST_YEAR_FLOOR:      # 2569 typed where 2026 is expected
        raise InvalidEvent(f"ปี {d.year} ดูเหมือนเป็น พ.ศ. แต่ช่องนี้ต้องเป็น ค.ศ. (ปี {d.year - 543}) — เลือกจากปฏิทินหรือแก้ปี")
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

    if len(str(ev.get("summary") or "")) > 500:
        raise InvalidEvent("สรุปยาวเกินไป")

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

    from app import repair_followup                      # the bills the repair board was waiting for: their "chase the bill" tickets are done
    repair_followup.on_bill_saved(payload.get("fixes_request_ids") or [])

    # A summary typed on the form (the manual-entry page has one). save_maintenance_event() doesn't take
    # it, so it is written right after; best effort -- the repair itself is already saved.
    typed = str(ev.get("summary") or "").strip()
    if typed:
        try:
            if history._has_summary():
                supabase.update("maintenance_events", {"id": f"eq.{event_id}"}, {"summary": typed})
        except supabase.SupabaseError:
            logger.exception("could not save the typed summary for event %s", event_id)

    return {
        "event_id": event_id,
        "attachments": [{"id": p["id"], "type": p["type"], "url": supabase.public_url(p["path"])} for p in prepared],
    }


# ------------------------------------------------------------ editing a saved event
class EventNotFound(Exception):
    """No confirmed event with that id."""


class MigrationMissing(Exception):
    """schema_v4.sql (the update function) has not been run in Supabase yet."""


def _validate_edit(payload: dict) -> None:
    ev = payload.get("event") or {}
    _parse_date(ev.get("event_date"))
    _num(ev.get("mileage"), "เลขไมล์", integer=True)
    _num(ev.get("next_service_mileage"), "ไมล์ครั้งต่อไป", integer=True)
    _num(ev.get("total_cost"), "ยอดรวม")
    for field, limit in (("vendor", 200), ("performed_by", 200), ("branch", 100), ("notes", 2000), ("summary", 500)):
        if len(str(ev.get(field) or "")) > limit:
            raise InvalidEvent(f"ข้อความในช่อง {field} ยาวเกินไป")
    vid = payload.get("vehicle_id")
    if vid:
        try:
            uuid.UUID(str(vid))
        except ValueError:
            raise InvalidEvent("รถที่เลือกไม่ถูกต้อง")
    lines = payload.get("line_items") or []
    if not lines:
        raise InvalidEvent("ต้องมีรายการอย่างน้อย 1 รายการ")
    for ln in lines:
        if not (ln.get("description") or "").strip():
            raise InvalidEvent("มีรายการที่ยังไม่ได้ใส่ชื่อ")
        if ln.get("category") and ln["category"] not in CATEGORIES:
            raise InvalidEvent(f"หมวด '{ln['category']}' ไม่ถูกต้อง")
        if ln.get("attachment_id"):
            try:
                uuid.UUID(str(ln["attachment_id"]))
            except ValueError:
                raise InvalidEvent("รายการอ้างอิงไฟล์ที่ไม่ถูกต้อง")
        _num(ln.get("quantity"), "จำนวน")
        _num(ln.get("unit_cost"), "ราคา/หน่วย")
        _num(ln.get("line_total"), "ราคารวมของรายการ")


def update_event(event_id: str, payload: dict) -> None:
    """
    Edits a SAVED event: header fields (and, if given, the truck) plus a full
    replacement of its line items, in one database transaction
    (update_maintenance_event() in schema_v4.sql). Photos, the AI's raw
    extraction and the board requests the event closed are left alone.
    """
    try:
        eid = str(uuid.UUID(str(event_id)))
    except ValueError:
        raise EventNotFound()
    _validate_edit(payload)
    vid = payload.get("vehicle_id")
    if vid and not supabase.select("vehicles", {"select": "id", "id": f"eq.{vid}", "limit": "1"}):
        raise InvalidEvent("ไม่พบรถคันนี้ในระบบ")
    ev = payload["event"]
    try:
        supabase.rpc("update_maintenance_event", {"p": {
            "event_id": eid,
            "vehicle_id": vid or None,
            "event_date": ev["event_date"],
            "mileage": ev.get("mileage"), "next_service_mileage": ev.get("next_service_mileage"), "total_cost": ev.get("total_cost"),
            "vendor": ev.get("vendor"), "performed_by": ev.get("performed_by"), "branch": ev.get("branch"),
            "notes": ev.get("notes"), "summary": ev.get("summary"),
            "line_items": [
                {
                    "attachment_id": ln.get("attachment_id") or None,
                    "description": ln["description"].strip(), "category": ln.get("category"),
                    "quantity": ln.get("quantity"), "unit": ln.get("unit"),
                    "unit_cost": ln.get("unit_cost"), "line_total": ln.get("line_total"),
                    "low_confidence_fields": ln.get("low_confidence_fields") or [],
                }
                for ln in payload["line_items"]
            ],
        }})
    except supabase.SupabaseError as exc:
        text = str(exc)
        if "PGRST202" in text or "Could not find the function" in text:
            raise MigrationMissing() from exc
        if "P0002" in text:
            raise EventNotFound() from exc
        raise


# ------------------------------------------------------------ deleting a record made by hand
def delete_event(event_id: str) -> None:
    """
    Deletes a record that was entered by hand (source 'manual') -- the way to undo a mis-click. A bill is never
    deleted (it is a document: correct it instead), and neither is a record that closed a repair request on the
    board. Its lines and attachments go with it, and so do its photos in storage.
    """
    try:
        eid = str(uuid.UUID(str(event_id)))
    except ValueError:
        raise EventNotFound()
    rows = supabase.select("maintenance_events", {"select": "id,source", "id": f"eq.{eid}", "limit": "1"})
    if not rows:
        raise EventNotFound()
    if rows[0]["source"] != "manual":
        raise InvalidEvent("ลบได้เฉพาะรายการที่กรอกเอง — บิลลบไม่ได้ แต่แก้ไขได้")
    closed = supabase.select("repair_requests", {"select": "seq", "fixed_event_id": f"eq.{eid}", "order": "seq.asc"})
    if closed:
        raise InvalidEvent("รายการนี้ปิดงานซ่อมไว้ (" + ", ".join(f"#{r['seq']}" for r in closed) + ") ลบไม่ได้")
    paths = [a["storage_path"] for a in supabase.select("event_attachments", {"select": "storage_path", "event_id": f"eq.{eid}"})]
    supabase.delete("maintenance_events", {"id": f"eq.{eid}"})
    if paths:
        try:
            supabase.storage_delete(paths)
        except supabase.SupabaseError:
            logger.exception("could not remove the photos of the deleted event %s", eid)
