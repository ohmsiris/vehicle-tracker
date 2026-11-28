"""
บัญชี › ลงข้อมูล › กล่องรับไฟล์ -- the ENGINE of the drop box (owner, 24 Nov 2026: "drop ANY monthly document; the app decides what it
is, its branch and its month from the CONTENT"). The contract the readers follow, and the helpers they share, are in app/inbox_common.py;
this module is everything around them:

* upload: each dropped file is stored once (sha256: the same file dropped again returns the row it already has, with a Thai message --
  never a second row), kept in the PRIVATE bucket (payroll and cheque sheets are never on a public link) at inbox/<id><ext>, identified
  and read straight away. Nothing is booked on upload: the owner looks at the preview first and presses ลงบัญชี.
* identify: inbox_common.sniff_file builds the Dropped once, then every exact reader's sniff() scores it; the best score at or above
  MIN_SNIFF wins. Nobody sure enough -> inbox_r_scan (only AI can read it, and only when the owner clicks). The file NAME never decides.
* status, from the preview (status_of): any open conflict -> needs_choice (the owner picks, e.g. which month: HQ forgets to update its
  titles, payroll PDF names may be stale), store_only -> stored, needs_ai -> needs_ai, else read. Booking sets booked.
* book: refused while a conflict is open. reader.book() REPLACES by batch key (re-dropping never doubles); the row keeps the keys it
  booked. When a later file books a key an older file had booked, the older row gives that key up (superseded) -- so removing the old
  file later can never delete what the new one booked. A row that lost ALL its keys becomes status "stored" with preview
  superseded_by / superseded_keys (row_view: superseded, superseded_by); a row that lost some stays booked and is only ever re-booked
  with the batches it still owns. ลงบัญชี on a fully superseded file is the owner's explicit "take the keys back". A reader may ask for
  "rebook" (a PEA bill stored after the cheque run that paid it: the cheque turns from a 5100 cost into "pays an existing bill"): every
  booked file of that kind and month is re-read and re-booked; a rebook that fails is reported (rebook_failed), never silently undone.
  The new booking is written FIRST; only the keys the earlier booking of this file held and the new one does not are then taken back.
  A reader's ValueError (acc_docs.BadBatch ...) is a Thai InboxError and the row is left as it was.
* one action per file at a time: book / choose / reread / reread pages / remove / ai hold a per-file lock; a second one is refused (Busy, 409).
* reread / choose: read again (a reader got better, the employees list changed, the owner answered). A booked file is re-booked only
  when the new read can book; otherwise the booking stays exactly as it is and the new read waits in preview["pending"] (row_view
  pending + its conflicts) for the owner. reread pages (28 Nov 2026): only the pages asked for are read again by the AI, from the
  images the first read stored (inbox_ai.reread_pages) -- for an advance list read before the reader took its deduction columns.
* remove: unbook (only the keys this file still owns; a reader's unbook may ask for rebooks, which run after), delete the stored file
  and the AI page images, delete the row.
* ai: only when the server has an Anthropic key and the owner clicked (the page shows the page count first). Runs in a background
  thread (status ai_running, the page polls every 3 s); app/inbox_ai.run gives the preview and the tokens used (ai_usage).
* checks: app/acc_checks.run(month, branch) -- the proofs that nothing is counted twice -- or a "not available yet" placeholder.

The readers (app/inbox_r_*.py), app/inbox_ai.py and app/acc_checks.py are written separately: every one is imported lazily and a missing
one is skipped, so the drop box works (and stores files) before all of them exist.

Pure parts (status_of, choose_reader, file_values, superseded, rebook_targets, clean_choices, row_view, files_by_feed) are unit-tested
in tests/test_inbox.py with fake readers; nothing here is ever tested against the live database.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Optional

from app import inbox_common as ic
from app import supabase

logger = logging.getLogger("vehicle_tracker.inbox")

TABLE = "inbox_files"
MAX_FILES = 30                     # per request (the page sends big drops a few files at a time)
MAX_FILE_BYTES = 50 * 1024 * 1024  # per file, the same as the accounting-files bucket (scans of a whole month are big)
TOO_BIG = f"ไฟล์ใหญ่เกิน {MAX_FILE_BYTES // (1024 * 1024)} MB ไม่ได้เก็บ (แยกไฟล์หรือลดขนาดก่อน)"
BUSY = "ไฟล์นี้กำลังทำรายการอยู่ รอสักครู่"
PAY_HIDDEN = "ไฟล์เงินเดือนรายคน — เปิดได้เฉพาะผู้มีเมนูรายชื่อพนักงาน (รวมค่าจ้าง)"
INLINE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif"}   # the only types /file shows in the browser
SCAN_READER = "inbox_r_scan"
TYPES = {  # extension -> media type; anything else is still accepted (the PEA AMR exports have no extension) as octet-stream
    ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".heic": "image/heic",
    ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12", ".csv": "text/csv", ".htm": "text/html", ".html": "text/html", ".txt": "text/plain",
}
STATUS_LABEL = {"read": "อ่านแล้ว รอลงบัญชี", "booked": "ลงบัญชีแล้ว", "needs_choice": "ต้องเลือกเดือน", "stored": "เก็บไว้ ไม่ลงบัญชี",
                "needs_ai": "อ่านด้วย AI ได้", "ai_running": "AI กำลังอ่าน...", "failed": "อ่านไม่สำเร็จ"}
_AI_STALE = "AI หยุดกลางคัน (เซิร์ฟเวอร์เริ่มใหม่) — กดอ่านด้วย AI อีกครั้ง"
# A booked file read again whose new read cannot book: the booking stays, the new read waits (preview["pending"]).
PENDING_MSG = {"needs_choice": "อ่านใหม่แล้วมีเรื่องต้องเลือก — ของที่ลงบัญชีไว้ยังอยู่เหมือนเดิมจนกว่าจะเลือก",
               "stored": "อ่านใหม่แล้วเป็นไฟล์เก็บไว้ ไม่ลงบัญชี — ของที่ลงบัญชีไว้ยังอยู่เหมือนเดิม (ลบไฟล์ถ้าไม่ต้องการ)",
               "needs_ai": "อ่านใหม่แล้วต้องให้ AI อ่าน — ของที่ลงบัญชีไว้ยังอยู่เหมือนเดิม",
               # อ่านหน้านี้ใหม่ of a booked file whose booking was refused: the new pages are kept (owner's review, 28 Nov 2026)
               "read": "อ่านหน้าใหม่แล้ว แต่ลงบัญชีใหม่ไม่สำเร็จ — ของที่ลงบัญชีไว้ยังอยู่เหมือนเดิม แก้ต้นเหตุแล้วกดอ่านใหม่เพื่อลงบัญชีอีกครั้ง"}


class InboxError(ValueError):
    """User-fixable problem; str() is a Thai message safe to show (main.py maps it to 422)."""


class NotFound(InboxError):
    """No such dropped file (404)."""


class Busy(InboxError):
    """Another action on this file is running right now (409)."""


class Forbidden(InboxError):
    """This person may not see this file (403): a payroll sheet without the employees menu."""


# ------------------------------------------------------------------ the readers (lazy: a missing one is skipped)
def _readers() -> list:
    """The reader modules that exist, in inbox_common.READERS order (the order also breaks a tie between two equal sniff scores)."""
    out = []
    for name in ic.READERS:
        try:
            out.append(importlib.import_module(f"app.{name}"))
        except ModuleNotFoundError as exc:
            if exc.name != f"app.{name}":          # the reader exists but something IT imports is missing: say so in the log
                logger.exception("inbox reader %s could not be imported", name)
        except Exception:                          # a broken reader must not take the drop box down with it
            logger.exception("inbox reader %s could not be imported", name)
    return out


def _optional(name: str):
    """app.inbox_ai / app.acc_checks, or None while they are not there yet."""
    try:
        return importlib.import_module(f"app.{name}")
    except ModuleNotFoundError as exc:
        if exc.name != f"app.{name}":
            logger.exception("%s could not be imported", name)
        return None


def _is_scan(reader) -> bool:
    return getattr(reader, "__name__", "").rsplit(".", 1)[-1] == SCAN_READER


def choose_reader(f: ic.Dropped, readers: list) -> tuple[Optional[object], float]:
    """(the reader that claims the file, its score). Every exact reader sniffs; the best score >= MIN_SNIFF wins (first in the list on a
    tie). Nobody sure enough -> the scan reader (score 0), or None when it is not there either."""
    best, score = None, 0.0
    for r in readers:
        if _is_scan(r):
            continue
        try:
            s = float(r.sniff(f) or 0)
        except Exception:                          # a reader that trips over a strange file simply does not claim it
            logger.exception("sniff failed in %s", getattr(r, "__name__", r))
            s = 0.0
        if s > score:
            best, score = r, s
    if best is not None and score >= ic.MIN_SNIFF:
        return best, score
    return next((r for r in readers if _is_scan(r)), None), 0.0


def reader_for(kind: Optional[str], readers: Optional[list] = None):
    """The reader whose KIND this is (None when that reader is not installed)."""
    for r in (readers if readers is not None else _readers()):
        if getattr(r, "KIND", None) == kind:
            return r
    return None


def unknown_preview(f: ic.Dropped) -> dict:
    """What a file gets when no reader claims it and the scan reader is not installed yet: kept, and offered to the AI."""
    pages = f.pages or (1 if f.is_scan else None)
    return ic.empty_preview("unknown", "ไฟล์ที่ยังไม่รู้จัก", needs_ai=True, pages=pages,
                            note="ยังไม่มีตัวอ่านที่รู้จักไฟล์นี้ — เก็บไฟล์ไว้แล้ว ให้ AI อ่านได้เมื่อกด")


def _normal(preview: dict, reader) -> dict:
    """Every key of the contract present (a reader may leave some out)."""
    base = ic.empty_preview(getattr(reader, "KIND", "unknown"), getattr(reader, "LABEL", "ไฟล์"))
    out = {**base, **(preview or {})}
    for k in ("signals", "conflicts", "batches", "flags"):
        out[k] = list(out.get(k) or [])
    return out


def read_file(name: str, data: bytes, choices: Optional[dict] = None, readers: Optional[list] = None) -> tuple[str, dict, Optional[str]]:
    """(kind, preview, error) -- what the file is and what it would book. Never writes; a reader may read the database."""
    readers = _readers() if readers is None else readers
    f = ic.sniff_file(name, data, choices)
    reader, _score = choose_reader(f, readers)
    if reader is None:
        p = unknown_preview(f)
        return p["kind"], p, None
    try:
        p = _normal(reader.read(f), reader)
    except Exception as exc:
        logger.exception("inbox read failed: %s (%s)", name, getattr(reader, "KIND", "?"))
        p = _normal({"pages": f.pages or None}, reader)
        return p["kind"], p, _thai_error("อ่านไฟล์ไม่สำเร็จ", exc)
    return p.get("kind") or getattr(reader, "KIND", "unknown"), p, None


def _thai_error(head: str, exc: Exception) -> str:
    msg = str(exc).strip()
    return f"{head}: {msg}"[:400] if msg else f"{head} ({type(exc).__name__})"


# ------------------------------------------------------------------ pure: status, the row's values, superseding, rebook
def status_of(preview: dict) -> str:
    if preview.get("store_only"):                  # kept, never booked: a month question would only be in the way (it files under its default)
        return "stored"
    if preview.get("conflicts"):
        return "needs_choice"
    if preview.get("needs_ai"):
        return "needs_ai"
    return "read"


def fully_superseded(preview: dict) -> bool:
    """Did newer files take over EVERY batch key of this preview? (Then the file is kept as "stored", not waiting to be booked.)"""
    taken = set(preview.get("superseded_keys") or [])
    keys = [b.get("key") for b in preview.get("batches") or []]
    return bool(preview.get("superseded_by")) and bool(keys) and all(k in taken for k in keys)


def file_values(kind: str, preview: dict, error: Optional[str]) -> dict:
    """The inbox_files columns that follow from a read (status / month / branches / preview / error)."""
    branches = sorted({b.get("branch") for b in preview.get("batches") or [] if b.get("branch")})
    if not branches:
        branches = sorted({s["value"] for s in preview.get("signals") or [] if s.get("what") == "branch" and s.get("value")})[:2]
    month = preview.get("month")
    if not month and preview.get("store_only"):
        month = next((c.get("default") for c in preview.get("conflicts") or [] if c.get("key") == "month"), None)
    status = "failed" if error else status_of(preview)
    if status == "read" and fully_superseded(preview):           # read again, but a newer file still owns everything it would book
        status = "stored"
    return {"kind": kind, "status": status, "month": month, "branches": branches,
            "preview": preview, "error": error, "ai_usage": preview.get("ai_usage")}


def superseded(rows: list[dict], keys: list[str], my_id: str) -> list[tuple[str, list[str]]]:
    """Other booked files that had booked one of these batch keys: [(id, the keys they keep)]. The newest booking owns a key."""
    mine = set(keys)
    out = []
    for r in rows:
        if r["id"] == my_id or r.get("status") != "booked":
            continue
        had = list(r.get("batches") or [])
        if mine & set(had):
            out.append((r["id"], [k for k in had if k not in mine]))
    return out


def supersede_values(other: dict, keep: list[str], by_name: str) -> dict:
    """The columns of an older booked row after a newer file took some (or all) of its keys: it remembers which keys and by whom; with
    none left it is kept as "stored" (superseded), never shown as waiting to be booked."""
    p = dict(other.get("preview") or {})
    taken = [k for k in other.get("batches") or [] if k not in set(keep)]
    p["superseded_keys"] = sorted(set(p.get("superseded_keys") or []) | set(taken))
    p["superseded_by"] = by_name
    values = {"batches": list(keep), "preview": p}
    if not keep:
        values.update(status="stored", booked_at=None, booked_total=None, error=None)
    return values


def base_preview(row: dict) -> dict:
    """The preview the row's booking stands on (without a re-read waiting for the owner)."""
    p = row.get("preview") or {}
    return {k: v for k, v in p.items() if k != "pending"} if "pending" in p else p


def pending_of(row: dict) -> Optional[dict]:
    """{"kind", "status", "preview", "choices"} of a re-read of a booked file that could not book, or None."""
    pend = (row.get("preview") or {}).get("pending")
    return pend if isinstance(pend, dict) and isinstance(pend.get("preview"), dict) else None


def rebook_targets(requests: list[dict], rows: list[dict], exclude: set) -> list[dict]:
    """The booked files a booking asked to re-book ({"kind", "month"} each), not the ones already done in this round."""
    want = {(q.get("kind"), q.get("month")) for q in requests or [] if q.get("kind") and q.get("month")}
    seen, out = set(), []
    for r in rows:
        if r.get("status") == "booked" and (r.get("kind"), r.get("month")) in want and r["id"] not in exclude and r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out


def valid_month(v) -> bool:
    return bool(re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", str(v or "")))


def clean_choices(preview: dict, old: dict, new: dict) -> dict:
    """The owner's answers merged with earlier ones. An answer to an open conflict must be one of its options -- except a month
    question with no options (no month found in the file): then any real month (YYYY-MM) the owner picks is accepted."""
    if not isinstance(new, dict) or not new:
        raise InboxError("ยังไม่ได้เลือก")
    open_ = {c.get("key"): c for c in preview.get("conflicts") or []}
    out = dict(old or {})
    for k, v in new.items():
        k, v = str(k), (None if v in (None, "") else str(v))
        c = open_.get(k)
        if c is not None and v is not None and k == "month" and not c.get("options"):
            if not valid_month(v):
                raise InboxError("เดือนไม่ถูกต้อง — เลือกเดือนใหม่")
        elif c is not None and v is not None and v not in {str(o.get("value")) for o in c.get("options") or []}:
            raise InboxError("ตัวเลือกไม่ถูกต้อง — เปิดดูไฟล์แล้วเลือกใหม่")
        if c is None and k not in out:
            raise InboxError("ไฟล์นี้ไม่มีเรื่องให้เลือกแล้ว — กดอ่านใหม่")
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def apply_choices(preview: dict, choices: dict) -> dict:
    """For a file the AI read (re-reading would spend tokens again): close the conflicts the owner answered; a month answer moves the
    preview and its batches to that month. inbox_ai.apply_choices, when it exists, does this better and is used instead."""
    p = {**preview, "conflicts": [c for c in preview.get("conflicts") or [] if c.get("key") not in choices]}
    if choices.get("month"):
        p["month"] = choices["month"]
        p["batches"] = [{**b, "month": choices["month"]} for b in p.get("batches") or []]
    return p


def owned_preview(row: dict) -> dict:
    """The row's preview cut down to the batches it still owns (a newer file may have taken some over)."""
    p = base_preview(row)
    keep = set(row.get("batches") or [])
    return {**p, "batches": [b for b in p.get("batches") or [] if b.get("key") in keep]}


def stale_part(old: dict, keys: list[str]) -> dict:
    """What an earlier booking (old: its owned preview) held that the new booking (keys) does not: the batches to take back once the
    new booking stood. A key the new booking replaces itself -- the same key, or one that starts with it + ":" -- is left alone."""
    new = set(keys)
    gone = [b for b in old.get("batches") or [] if b.get("key") not in new and not any(k.startswith(f"{b.get('key')}:") for k in new)]
    return {**old, "batches": gone}


def hide_pay(preview: dict) -> dict:
    """A payroll preview without anyone's pay: the per-person wage rows become one total row, employee_pay_months rows are dropped.
    For a person who can open ลงข้อมูล but not รายชื่อพนักงาน (รวมค่าจ้าง)."""
    def cut(p: dict) -> dict:
        out = {**p, "batches": []}
        for b in p.get("batches") or []:
            rows = b.get("rows") or []
            people = [r for r in rows if r.get("role") == "wages" and r.get("code")]
            rest = [r for r in rows if not (r.get("role") == "wages" and r.get("code"))]
            if people:
                rest.insert(0, {"date": people[0].get("date"), "text": f"ค่าจ้างรวม {len(people)} คน (รายคนดูได้ที่เมนูรายชื่อพนักงาน)",
                                "party": None, "amount": round(sum(float(r.get("amount") or 0) for r in people), 2), "role": "wages",
                                "account": people[0].get("account"), "flag": None})
            nb = {**b, "rows": rest}
            if "pay_months" in nb:
                nb["pay_months"] = None
            out["batches"].append(nb)
        return out
    out = cut(preview or {})
    pend = (preview or {}).get("pending")
    if isinstance(pend, dict) and isinstance(pend.get("preview"), dict):
        out["pending"] = {**pend, "preview": cut(pend["preview"])}
    return out


def serve_as(mime: Optional[str]) -> tuple[str, bool]:
    """(the media type /file sends, shown inline?). Only PDFs and pictures are shown in the browser; anything else (an .html dropped in
    the box ...) is a download, never active content on the app's own address."""
    m = (mime or "").lower()
    return (m, True) if m in INLINE_TYPES else ("application/octet-stream", False)


def ai_image_paths(preview: Optional[dict]) -> list[str]:
    """The AI page images of a preview (and of a re-read waiting in it) -- only the drop box's own (accdocs/inbox/...)."""
    out: list = []
    for p in (preview or {}, ((preview or {}).get("pending") or {}).get("preview") or {}):
        imgs = p.get("ai_images") if isinstance(p, dict) else None
        vals = imgs.values() if isinstance(imgs, dict) else (imgs if isinstance(imgs, list) else [])
        out += [v for v in vals if isinstance(v, str) and v.startswith("accdocs/inbox/") and v not in out]
    return out


def _num(x) -> Optional[float]:
    try:
        return None if x is None else round(float(x), 2)
    except (TypeError, ValueError):
        return None


def checkable(preview: dict) -> bool:
    """Opened on ตรวจเอกสารก่อนลงบัญชี: an AI-read expense paper whose rows say where they go (key / dest_kind). One read before that
    page (28 Nov 2026) has neither -- it keeps the dialog (its อ่านใหม่, no AI cost, gives them) instead of showing empty pages."""
    if not preview.get("acc_docs_batch"):
        return False
    rows = ((preview.get("batches") or [{}])[0] or {}).get("rows") or []
    return any(r.get("key") and r.get("dest_kind") for r in rows)


def row_view(row: dict, running: Optional[set] = None) -> dict:
    """One dropped file as the list shows it (the full preview stays on the server until the owner opens it)."""
    p = base_preview(row)
    pend = pending_of(row)
    batches = p.get("batches") or []
    status, error = row.get("status"), row.get("error")
    if status == "ai_running" and running is not None and row["id"] not in running:
        status, error = "failed", _AI_STALE         # the job died with the server: offer the button again
    ft = [_num(b.get("file_total")) for b in batches]
    rt = [_num(b.get("rows_total")) for b in batches]
    sup_by = p.get("superseded_by") or None
    return {"id": row["id"], "file_name": row.get("file_name"), "kind": row.get("kind"), "label": p.get("label") or row.get("kind") or "ไฟล์",
            "status": status, "status_label": STATUS_LABEL.get(status, status), "month": row.get("month"), "branches": row.get("branches") or [],
            "uploaded_at": row.get("uploaded_at"), "uploaded_by": row.get("uploaded_by"), "booked_at": row.get("booked_at"),
            "booked_total": _num(row.get("booked_total")), "error": error, "ai_usage": row.get("ai_usage"), "note": p.get("note") or "",
            "pages": p.get("pages"), "needs_ai": bool(p.get("needs_ai")), "store_only": bool(p.get("store_only")),
            "superseded": bool(sup_by) and status == "stored", "superseded_by": sup_by,
            "remove_note": p.get("remove_note") or "",
            "check": checkable(p),                         # an AI-read expense paper: opened on ตรวจเอกสารก่อนลงบัญชี (28 Nov 2026)
            "pending": ({"status": pend.get("status"), "status_label": STATUS_LABEL.get(pend.get("status"), pend.get("status")),
                         "message": PENDING_MSG.get(pend.get("status"), ""), "note": (pend["preview"].get("note") or "")} if pend else None),
            "conflicts": ((pend["preview"] if pend else p).get("conflicts") or []),
            "choices": (pend.get("choices") if pend else None) or row.get("choices") or {}, "flags": len(p.get("flags") or []),
            "mime_type": row.get("mime_type"), "byte_size": row.get("byte_size"),
            "batches": [{"key": b.get("key"), "feed": b.get("feed"), "branch": b.get("branch"), "month": b.get("month"), "title": b.get("title"),
                         "file_total": _num(b.get("file_total")), "rows_total": _num(b.get("rows_total")), "rows": len(b.get("rows") or []),
                         "booked": b.get("key") in (row.get("batches") or [])} for b in batches],
            "file_total": round(sum(x for x in ft if x is not None), 2) if any(x is not None for x in ft) else None,
            "rows_total": round(sum(x for x in rt if x is not None), 2) if any(x is not None for x in rt) else None}


def files_by_feed(rows: list[dict], branch: str = "") -> dict:
    """{feed: [{id, file_name, status, booked}]} -- the checklist says which dropped file(s) each row came from. A file counts for a
    feed through its batches (their branch must match when one branch is shown); booked = that batch is booked by this file."""
    out: dict = {}
    for r in rows:
        if r.get("status") in ("failed", "ai_running", "needs_ai", "stored"):     # stored includes a file a newer one replaced
            continue
        owned = set(r.get("batches") or [])
        p = base_preview(r)
        taken = set(p.get("superseded_keys") or [])
        for b in p.get("batches") or []:
            feed = b.get("feed")
            if not feed or b.get("key") in taken or (branch and b.get("branch") and b.get("branch") != branch):
                continue
            lst = out.setdefault(feed, [])
            booked = r.get("status") == "booked" and b.get("key") in owned
            hit = next((x for x in lst if x["id"] == r["id"]), None)
            if hit:
                hit["booked"] = hit["booked"] or booked
            else:
                lst.append({"id": r["id"], "file_name": r.get("file_name"), "status": r.get("status"), "booked": booked})
    return out


# ------------------------------------------------------------------ database
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_id(file_id: str) -> str:
    try:
        return str(uuid.UUID(str(file_id)))
    except ValueError:
        raise NotFound("ไม่พบไฟล์นี้")


# One action per file at a time (two clicks on ลงบัญชี, two tabs, a rebook from another file): the second one is refused, never queued.
_file_locks: dict = {}
_file_locks_guard = threading.Lock()


@contextmanager
def _busy(file_id: str):
    fid = _check_id(file_id)
    with _file_locks_guard:
        lock = _file_locks.setdefault(fid, threading.RLock())     # re-entrant: book() from reread() on the same thread
    if not lock.acquire(blocking=False):
        raise Busy(BUSY)
    try:
        yield
    finally:
        lock.release()


def _get(file_id: str) -> dict:
    _check_id(file_id)
    rows = supabase.select(TABLE, {"select": "*", "id": f"eq.{file_id}", "limit": "1"})
    if not rows:
        raise NotFound("ไม่พบไฟล์นี้ (อาจถูกลบไปแล้ว)")
    return rows[0]


def _save(file_id: str, values: dict) -> dict:
    rows = supabase.update(TABLE, {"id": f"eq.{file_id}"}, values)
    return rows[0] if rows else {**_get(file_id)}


def _download(row: dict) -> bytes:
    return supabase.storage_download(row["storage_path"], bucket=supabase.ACCOUNTING_BUCKET)


def _ext(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def fix_name(name: str) -> str:
    """A Thai file name some tools send in the Windows Thai code page arrives as Latin-1 garbage ("ãºÊÃØ»..."): put it back."""
    if not name or any("฀" <= ch <= "๿" for ch in name) or not any(" " <= ch <= "ÿ" for ch in name):
        return name
    try:
        raw = name.encode("latin-1")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "cp874"):
        try:
            fixed = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if any("฀" <= ch <= "๿" for ch in fixed):
            return fixed
    return name


def upload(files: list[tuple[str, Optional[bytes]]], who: str = "") -> list[dict]:
    """Store, identify and read each dropped file. Returns one row per file (row_view) with a Thai "message"; a file already in the box
    returns its existing row (dedupe by sha256). An empty file, or one over MAX_FILE_BYTES (data None: main.py stopped reading it), is
    refused on its own without failing the others."""
    if not files:
        raise InboxError("เลือกไฟล์อย่างน้อยหนึ่งไฟล์")
    if len(files) > MAX_FILES:
        raise InboxError(f"ครั้งละไม่เกิน {MAX_FILES} ไฟล์")
    readers = _readers()
    out, seen = [], {}
    for name, data in files:
        name = fix_name(name or "file")[:200]
        if data is None or len(data) > MAX_FILE_BYTES:
            out.append({"id": None, "file_name": name, "status": "failed", "status_label": STATUS_LABEL["failed"], "message": f"{name}: {TOO_BIG}"})
            continue
        if not data:
            out.append({"id": None, "file_name": name, "status": "failed", "status_label": STATUS_LABEL["failed"], "message": f"{name}: ไฟล์ว่าง ไม่ได้เก็บ"})
            continue
        sha = hashlib.sha256(data).hexdigest()
        if sha in seen:                                    # the same file twice in one drop
            out.append({**seen[sha], "message": f"{name}: ไฟล์เดียวกับที่เพิ่งส่ง — ไม่เพิ่มซ้ำ"})
            continue
        have = supabase.select(TABLE, {"select": "*", "sha256": f"eq.{sha}", "limit": "1"})
        if have:
            v = row_view(have[0], _running_ids())
            seen[sha] = v
            out.append({**v, "message": f"{name}: ไฟล์นี้อยู่ในกล่องแล้ว ({v['file_name']}) — ไม่เพิ่มซ้ำ"})
            continue
        v = _store_new(name, data, sha, who, readers)
        seen[sha] = v
        out.append(v)
    return out


def _store_new(name: str, data: bytes, sha: str, who: str, readers: list) -> dict:
    kind, preview, error = read_file(name, data, {}, readers)
    fid = str(uuid.uuid4())
    ext = _ext(name) or (".pdf" if data[:5] == b"%PDF-" else "")
    path = f"inbox/{fid}{ext}"
    mime = TYPES.get(ext, "application/octet-stream")
    supabase.storage_upload(path, data, mime, bucket=supabase.ACCOUNTING_BUCKET)
    row = {"id": fid, "file_name": name, "storage_path": path, "mime_type": mime, "byte_size": len(data), "sha256": sha,
           "uploaded_by": (who or "")[:80] or None, "choices": {}, **file_values(kind, preview, error)}
    try:
        saved = supabase.insert(TABLE, [row])
    except supabase.SupabaseError:
        supabase.storage_delete([path], bucket=supabase.ACCOUNTING_BUCKET)
        have = supabase.select(TABLE, {"select": "*", "sha256": f"eq.{sha}", "limit": "1"})
        if have:                                           # dropped twice at the same moment: the other one won
            return {**row_view(have[0], _running_ids()), "message": f"{name}: ไฟล์นี้อยู่ในกล่องแล้ว — ไม่เพิ่มซ้ำ"}
        raise
    v = row_view(saved[0] if saved else row, _running_ids())
    msg = {"read": "อ่านแล้ว — เปิดดูก่อนลงบัญชี", "needs_choice": "อ่านแล้ว — ข้อมูลในไฟล์ไม่ตรงกัน ต้องเลือก", "stored": "เก็บไว้ (ไม่ลงบัญชี)",
           "needs_ai": "ต้องให้ AI อ่าน", "failed": error or "อ่านไม่สำเร็จ"}.get(v["status"], "")
    return {**v, "message": f"{name}: {msg}"}


def _is_payroll(row: dict) -> bool:
    return row.get("kind") == "payroll" or (pending_of(row) or {}).get("kind") == "payroll"


def get(file_id: str, see_pay: bool = True, hr: str = "none") -> dict:
    """One file with its full preview (the preview dialog) and the role names. see_pay False (no employees menu): a payroll file shows
    its totals only. hr: may the caller record in พนักงาน -- "ok" / "locked" (HR PIN not given) / "none" -- for the check page's
    บันทึกในพนักงาน (app/inbox_hr.view: what is there now is only worked out for "ok"; owner, 28 Nov 2026)."""
    row = _get(file_id)
    preview = row.get("preview") or {}
    if not see_pay and _is_payroll(row):
        preview = hide_pay(preview)
    pend = pending_of({"preview": preview})
    out = {**row_view(row, _running_ids()), "preview": base_preview({"preview": preview}),
           "pending_preview": pend["preview"] if pend else None, "roles": ic.ROLES}
    if out["preview"].get("acc_docs_batch"):          # ตรวจเอกสารก่อนลงบัญชี: the pick lists of its line editor (28 Nov 2026)
        ai = _optional("inbox_ai")
        if ai is not None:
            out["accounts"] = ai.chart_list()
            out["truck_accounts"] = list(ai.TRUCK_ACCOUNTS)
            out["vehicles"] = [{k: v.get(k) for k in ("id", "vehicle_number", "plate_number", "branch")} for v in ai._vehicles()]
        out["edits_version"] = edits_version(out["preview"])
        hr_mod = _optional("inbox_hr")
        if hr_mod is not None:
            out["hr"] = hr_mod.view(out["preview"], hr, ai_ready())
    return out


def edits_version(preview: dict) -> str:
    """A fingerprint of the fixes saved on a file (preview["line_edits"]): the check page sends back the one it opened with, so a tab
    opened before someone else saved cannot wipe their fixes (28 Nov 2026)."""
    le = preview.get("line_edits") if isinstance(preview.get("line_edits"), dict) else {}
    if not le.get("edits") and not le.get("added") and not le.get("rows"):      # rows: the per-person tables (29 Nov 2026)
        le = {}
    return hashlib.sha256(json.dumps(le, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def page_image(file_id: str, page: int, see_pay: bool = True) -> bytes:
    """One page of an AI-read file as the AI saw it (JPEG, private bucket) -- for ตรวจเอกสารก่อนลงบัญชี, before anything is booked.
    Only the drop box's own pictures (accdocs/inbox/<file id>/p<n>.jpg); a payroll file only for whoever may see pay."""
    row = _get(file_id)
    if not see_pay and _is_payroll(row):
        raise Forbidden(PAY_HIDDEN)
    for p in (base_preview(row), (pending_of(row) or {}).get("preview") or {}):
        imgs = p.get("ai_images") if isinstance(p.get("ai_images"), dict) else {}
        path = imgs.get(str(page)) or imgs.get(page)
        if isinstance(path, str) and re.fullmatch(r"accdocs/inbox/[0-9a-f-]{36}/p\d{1,3}\.jpg", path):
            return supabase.storage_download_private(path)
    raise NotFound("ไม่มีรูปหน้านี้")


def save_edits(file_id: str, line_edits: dict, see_pay: bool = True, version: Optional[str] = None, hr: str = "none") -> dict:
    """บันทึกการแก้ on ตรวจเอกสารก่อนลงบัญชี (owner, 28 Nov 2026): the owner's fixes to the lines of an AI-read expense paper, checked
    strictly (inbox_ai.clean_line_edits), stored in the preview (line_edits) and the preview rebuilt with them the way อ่านใหม่ does --
    the per-person tables of the advance pages too (line_edits["rows"], inbox_ai.clean_row_edits; owner, 29 Nov 2026: "and how do I
    edit the numbers": the fixed rows are the advances and deductions the pay page uses, and the page's booked 1500 line their sum) --
    no AI call. A booked file is booked again at once with the fixes (as a re-read that can book is). version: edits_version of the
    fixes the page opened with (the route always passes it): fixes saved since -- another tab, the bookkeeper -- refuse the save instead
    of being wiped. Returns what get() does."""
    ai = _optional("inbox_ai")
    if ai is None:
        raise InboxError("ส่วนอ่านด้วย AI ยังไม่พร้อม")
    with _busy(file_id):
        row = _get(file_id)
        _refuse_running(row)
        if pending_of(row):
            raise InboxError("อ่านใหม่แล้วยังมีเรื่องต้องเลือก — เลือกก่อน แล้วค่อยแก้รายการ")
        base = base_preview(row)
        if not base.get("acc_docs_batch") or not _ai_read(row):
            raise InboxError("ไฟล์นี้ไม่ใช่เอกสารเบิกที่อ่านด้วย AI — แก้รายการที่นี่ไม่ได้")
        if version is not None and version != edits_version(base):
            raise InboxError("มีการแก้ไฟล์นี้จากที่อื่นแล้ว — เปิดหน้าตรวจใหม่ (การแก้ในหน้านี้ยังไม่ได้บันทึก)")
        try:
            clean = ai.clean_line_edits(base, line_edits, ai._vehicles())
        except ValueError as exc:
            raise InboxError(str(exc))
        stored = {**base}
        if ai.has_edits(clean):
            stored["line_edits"] = clean
        else:
            stored.pop("line_edits", None)
        v, booked = _reprocess({**row, "preview": stored}, row.get("choices") or {}, {row["id"]})
    out = get(file_id, see_pay, hr)
    return {**out, "booked_again": booked, "rebook_failed": v.get("rebook_failed") or [], "message": v.get("message") or ""}


def reread_pages(file_id: str, pages, see_pay: bool = True, hr: str = "none") -> dict:
    """อ่านหน้านี้ใหม่ on ตรวจเอกสารก่อนลงบัญชี (owner, 28 Nov 2026): these pages of an AI-read file read again by the AI from the
    images stored at the first read, with the reader's current schema (inbox_ai.reread_pages: one paid call a page) -- a mid-month
    advance list read before the reader returned ส่วนกลาง / ความผิด / อุปกรณ์ gets them without paying for the whole file again. The
    preview is rebuilt keeping the owner's line fixes and บันทึกในพนักงาน; a booked file is booked again as a re-read that can book is
    (_reprocess). One action per file (busy lock), refused while the AI reads it or a re-read waits for a choice. The tokens are added
    to the file's ai_usage -- a read that failed after a call still counts them. Returns what get() does, with "reread"
    ({pages, failed}), "booked_again", "rebook_failed" and "message"."""
    from app.config import settings
    if not settings.anthropic_api_key:
        raise InboxError("ยังไม่ได้ตั้งค่า AI บนเซิร์ฟเวอร์ (ANTHROPIC_API_KEY)")
    ai = _optional("inbox_ai")
    if ai is None or not hasattr(ai, "reread_pages"):
        raise InboxError("ส่วนอ่านด้วย AI ยังไม่พร้อม")
    with _busy(file_id):
        row = _get(file_id)
        if not see_pay and _is_payroll(row):
            raise Forbidden(PAY_HIDDEN)
        _refuse_running(row)
        pend = pending_of(row)
        if pend and pend.get("status") == "read":          # an earlier page re-read whose booking was refused waits (_reprocess)
            raise InboxError(PENDING_MSG["read"])
        if pend:
            raise InboxError("อ่านใหม่แล้วยังมีเรื่องต้องเลือก — เลือกก่อน แล้วค่อยอ่านหน้าใหม่")
        base = base_preview(row)
        if not _ai_read(row) or not base.get("ai_pages"):
            raise InboxError("ไฟล์นี้ไม่มีหน้าที่ AI อ่านไว้ — อ่านหน้าใหม่ไม่ได้")
        try:
            new = ai.reread_pages({**base, "ai_usage": row.get("ai_usage") or base.get("ai_usage")}, pages, row.get("choices") or {})
        except ai.InboxAIError as exc:
            spent = getattr(exc, "usage", None)
            if spent and spent.get("calls") and spent != (row.get("ai_usage") or base.get("ai_usage")):
                _save(row["id"], {"preview": {**(row.get("preview") or {}), "ai_usage": spent}, "ai_usage": spent})
            raise InboxError(str(exc))
        done = new.pop("reread", None) or {}
        v, booked = _reprocess({**row, "ai_usage": new.get("ai_usage")}, row.get("choices") or {}, {row["id"]}, fresh=new)
    out = get(file_id, see_pay, hr)
    return {**out, "reread": done, "booked_again": booked, "rebook_failed": v.get("rebook_failed") or [], "message": v.get("message") or ""}


def download(file_id: str, see_pay: bool = True) -> tuple[bytes, str, str, bool]:
    """(bytes, media type to send, file name, show inline?). Only PDFs and pictures are shown inline (serve_as)."""
    row = _get(file_id)
    if not see_pay and _is_payroll(row):
        raise Forbidden(PAY_HIDDEN)
    media, inline = serve_as(row.get("mime_type"))
    return _download(row), media, row.get("file_name") or "file", inline


def _refuse_running(row: dict) -> None:
    """A live AI read blocks the file; a row left in ai_running by a restart (its job is gone) does not."""
    if row.get("status") == "ai_running" and row["id"] in _running_ids():
        raise InboxError("AI กำลังอ่านไฟล์นี้อยู่ — รอสักครู่")


def choose(file_id: str, choices: dict) -> dict:
    """The owner's answer to a conflict (e.g. {"month": "2026-11"}): read again with it; a booked file is re-booked when it can."""
    with _busy(file_id):
        row = _get(file_id)
        _refuse_running(row)
        pend = pending_of(row)
        if pend:                                           # a booked file's re-read is waiting: the question is the re-read's
            merged = clean_choices(pend["preview"], pend.get("choices") or {}, choices)
        else:
            merged = clean_choices(row.get("preview") or {}, row.get("choices") or {}, choices)
        return _reprocess(row, merged, {row["id"]})[0]


def reread(file_id: str) -> dict:
    with _busy(file_id):
        row = _get(file_id)
        _refuse_running(row)
        pend = pending_of(row)
        return _reprocess(row, (pend.get("choices") if pend else None) or row.get("choices") or {}, {row["id"]})[0]


def _ai_read(row: dict) -> bool:
    """Was this file's preview made by the AI? (Then re-reading does not run the AI again: choices are applied to what it read.)"""
    p = base_preview(row)
    return bool(row.get("ai_usage") or p.get("ai_usage")) and not p.get("needs_ai")


def _carry(base: dict, new: dict) -> dict:
    """What a new read keeps from the old preview: which of its keys newer files took over."""
    if base.get("superseded_by"):
        return {**new, "superseded_by": base["superseded_by"], "superseded_keys": list(base.get("superseded_keys") or [])}
    return new


def _reprocess(row: dict, choices: dict, visited: set, fresh: Optional[dict] = None) -> tuple[dict, bool]:
    """Read the file again (with these choices). (row_view, did it book?).
    Not booked: the new read replaces the old one. Booked: READ FIRST. Only a read that can book replaces the booking (the new booking
    is written, then whatever the old one held and the new one does not is taken back). A read with a question, a store-only read or a
    failed read leaves the booking exactly as it is; the question waits in preview["pending"] for the owner.
    fresh: a new read made already (reread_pages: some pages read again by the AI) -- used as it is, never rebuilt a second time.
    A paid re-read is never thrown away (owner's review, 28 Nov 2026): parked, it saves its tokens (ai_usage) with it; a later choose /
    reread of the file rebuilds from the page reads waiting in pending (the newest), not the booked ones; and when booking it is refused,
    the new read and its tokens are parked beside the booking (pending status "read") before the refusal is raised."""
    was_booked = row.get("status") == "booked"
    base = base_preview(row)
    if fresh is not None:
        preview = _normal(fresh, None)
        kind, error = row.get("kind") or preview.get("kind"), None
    elif _ai_read(row):
        ai = _optional("inbox_ai")
        fn = getattr(ai, "apply_choices", None) if ai else None
        pend = pending_of(row)
        newest = (pend or {}).get("preview") or {}
        # a re-read waiting beside the booking (reread_pages) holds the newer page reads: those are what the answer applies to
        # (with its fixes: the re-read dropped those of a page whose lines moved -- the booked ones must not come back onto other lines)
        src = ({**base, "ai_pages": newest["ai_pages"], "ai_usage": newest.get("ai_usage") or base.get("ai_usage"), "line_edits": newest.get("line_edits")}
               if newest.get("ai_pages") else base)
        preview = _normal(fn(src, choices) if fn else apply_choices(src, choices), None)
        kind, error = row.get("kind") or preview.get("kind"), None
    else:
        kind, preview, error = read_file(row["file_name"], _download(row), choices)
    preview = _carry(base, {k: v for k, v in preview.items() if k != "pending"})
    values = {**file_values(kind, preview, error), "choices": choices, "ai_usage": row.get("ai_usage") or preview.get("ai_usage")}
    if not was_booked:
        return row_view(_save(row["id"], values), _running_ids()), False
    if error:                                              # keep what is booked rather than lose it to a failed read
        raise InboxError(f"{error} — ของที่ลงบัญชีไว้ยังอยู่เหมือนเดิม")
    spent = {"ai_usage": values["ai_usage"]} if fresh is not None else {}           # the tokens of a paid re-read are saved with it
    if values["status"] != "read":
        parked = {**base, **spent, "pending": {"kind": kind, "status": values["status"], "preview": preview, "choices": choices}}
        saved = _save(row["id"], {"preview": parked, **spent})
        return {**row_view(saved, _running_ids()), "message": PENDING_MSG.get(values["status"], "")}, False
    new_row = {**row, "kind": kind, "preview": preview, "choices": choices}
    if fresh is None:
        return _book(new_row, visited, old=row, extra=values), True
    try:
        return _book(new_row, visited, old=row, extra=values), True
    except InboxError:                                     # the reader refused it: the booking stays, the paid read waits beside it
        _park_quietly(row["id"], {"preview": {**base, **spent, "pending": {"kind": kind, "status": "read", "preview": preview, "choices": choices}}, **spent})
        raise
    except Exception:                                      # anything else: at least the tokens are counted
        _park_quietly(row["id"], {"preview": {**base, **spent}, **spent})
        raise


def _park_quietly(file_id: str, values: dict) -> None:
    """Save what a failed re-booking must not lose; a failure here is logged, never hides the first error."""
    try:
        _save(file_id, values)
    except Exception:                                      # noqa: BLE001
        logger.exception("inbox: keeping the re-read of %s failed", file_id)


def _unbook_preview(kind: Optional[str], preview: dict) -> dict:
    """The reader's unbook (or the default one) of these batches. A reader may answer {"rebook": [...]}: the caller runs those."""
    if not preview.get("batches"):
        return {}
    reader = reader_for(kind)
    fn = getattr(reader, "unbook", None) if reader else None
    res = (fn or ic.unbook_batches)(preview)
    return res if isinstance(res, dict) else {}


def _unbook(row: dict) -> dict:
    """Remove what this file booked (only the keys it still owns)."""
    if not row.get("batches"):
        return {}
    return _unbook_preview(row.get("kind"), owned_preview(row))


def _bookable(row: dict) -> None:
    """Refuse ลงบัญชี with the reason, in Thai."""
    p = base_preview(row)
    status = row.get("status")
    pend = pending_of(row)
    if pend:
        raise InboxError(PENDING_MSG.get(pend.get("status"), "อ่านใหม่แล้วยังลงบัญชีไม่ได้") +
                         (" (" + " · ".join(c.get("message", "") for c in pend["preview"].get("conflicts") or []) + ")"
                          if pend["preview"].get("conflicts") else ""))
    if p.get("conflicts") or status == "needs_choice":
        raise InboxError("ยังมีเรื่องที่ต้องเลือกก่อน (" + " · ".join(c.get("message", "") for c in p.get("conflicts") or []) + ")")
    take_back = status == "stored" and fully_superseded(p)
    if (status == "stored" or p.get("store_only")) and not take_back:
        raise InboxError("ไฟล์นี้เก็บไว้เฉย ๆ ไม่ลงบัญชี" + (f" — {p['note']}" if p.get("note") else ""))
    if status in ("needs_ai", "ai_running") or p.get("needs_ai"):
        raise InboxError("ไฟล์นี้ยังไม่ได้อ่าน — กดอ่านด้วย AI ก่อน")
    if status == "failed":
        raise InboxError("ไฟล์นี้อ่านไม่สำเร็จ — กดอ่านใหม่")


def book(file_id: str, visited: Optional[set] = None) -> dict:
    """ลงบัญชี: the reader books the preview (replacing by batch key), the row records what it booked; then the rebooks it asked for.
    On a fully superseded file this is the owner taking its keys back from the newer file."""
    with _busy(file_id):
        row = _get(file_id)
        _bookable(row)
        visited = set() if visited is None else visited
        return _book(row, visited, old=row if row.get("status") == "booked" else None)


def _book(row: dict, visited: set, old: Optional[dict] = None, extra: Optional[dict] = None) -> dict:
    """Book row["preview"] (the caller checked it may be booked). old: the row as booked before (its owned batches are taken back
    AFTER the new booking stood, and only those the new one does not replace). extra: columns to save with it (a re-read's)."""
    fid = row["id"]
    visited.add(fid)
    p = base_preview(row)
    take_back = row.get("status") == "stored" and fully_superseded(p)
    taken = set() if take_back else set(p.get("superseded_keys") or [])
    batches = [b for b in p.get("batches") or [] if b.get("key") not in taken]     # never take a key back from a newer file silently
    if not batches and not p.get("books_without_batches"):
        raise InboxError("ไม่มีรายการให้ลงบัญชีในไฟล์นี้" + (" (ไฟล์ที่ใหม่กว่าแทนที่ไปหมดแล้ว)" if taken and p.get("batches") else ""))
    reader = reader_for(row.get("kind"))
    if reader is None and not _ai_read(row):
        raise InboxError("ยังไม่มีตัวลงบัญชีของไฟล์ประเภทนี้ในระบบ")
    to_book = {**p, "batches": batches}
    try:
        res = (reader.book(to_book, fid) if reader is not None else ic.book_batches(to_book)) or {}
    except InboxError:
        raise
    except ValueError as exc:                              # the reader's own Thai refusal (acc_docs.BadBatch, InvalidEntry ...)
        logger.warning("inbox book refused for %s: %s", fid, exc)
        raise InboxError(_thai_error("ลงบัญชีไม่สำเร็จ", exc) + (" — ของที่ลงบัญชีไว้ก่อนหน้ายังไม่ถูกถอน" if old is not None else ""))
    keys = list(res.get("batches") or [b["key"] for b in batches])
    unbooked = _unbook_preview((old or {}).get("kind"), stale_part(owned_preview(old), keys)) if old is not None and old.get("batches") else {}
    for other in _rows_with_keys(keys):
        if other["id"] == fid or other.get("status") != "booked":
            continue
        keep = [k for k in other.get("batches") or [] if k not in set(keys)]
        if keep != list(other.get("batches") or []):
            _save(other["id"], supersede_values(other, keep, row.get("file_name") or ""))
    newp = {k: v for k, v in p.items() if k not in ("superseded_keys", "superseded_by")}
    still = [k for k in (p.get("superseded_keys") or []) if k not in set(keys)] if not take_back else []
    if still:
        newp.update(superseded_keys=still, superseded_by=p.get("superseded_by"))
    values = {**(extra or {}), "status": "booked", "batches": keys, "booked_total": _num(res.get("booked")), "booked_at": _now(),
              "error": None, "preview": newp}
    saved = _save(fid, values)
    rebooked, failed = _run_rebooks(list(res.get("rebook") or []) + list(unbooked.get("rebook") or []), visited)
    return {**row_view(saved, _running_ids()), "rebooked": rebooked, "rebook_failed": failed}


def _run_rebooks(requests: list[dict], visited: set) -> tuple[int, list[dict]]:
    """Re-read and re-book the booked files these requests name. (how many booked again, [{id, file_name, error}] of those that did
    not). A failure is reported, never raised: what asked for the rebook stays done."""
    requests = [q for q in requests or [] if isinstance(q, dict) and q.get("kind") and q.get("month")]
    if not requests:
        return 0, []
    months = sorted({q["month"] for q in requests})
    n, failed = 0, []
    for r in rebook_targets(requests, _booked_rows(months), visited):
        visited.add(r["id"])
        try:
            with _busy(r["id"]):
                fresh = _get(r["id"])
                if fresh.get("status") != "booked":
                    continue
                pend = pending_of(fresh)
                v, ok = _reprocess(fresh, (pend.get("choices") if pend else None) or fresh.get("choices") or {}, visited)
            if ok:
                n += 1
            else:
                failed.append({"id": r["id"], "file_name": r.get("file_name"), "error": v.get("message") or "ลงบัญชีใหม่ไม่ได้ — เปิดดูไฟล์"})
        except Exception as exc:                           # noqa: BLE001 -- reported to the page, logged here
            logger.exception("rebook of %s failed", r["id"])
            msg = str(exc) if isinstance(exc, InboxError) else _thai_error("ลงบัญชีใหม่ไม่สำเร็จ", exc)
            failed.append({"id": r["id"], "file_name": r.get("file_name"), "error": msg})
    return n, failed


def _booked_rows(months: Optional[list] = None) -> list[dict]:
    params = {"select": "id,kind,month,status,batches,choices,file_name,storage_path,preview,ai_usage", "status": "eq.booked", "order": "uploaded_at.asc"}
    if months:
        params["month"] = f"in.({','.join(months)})"
    return supabase.select_all(TABLE, params)


def _rows_with_keys(keys: list[str]) -> list[dict]:
    """Booked files whose batch keys overlap these (PostgREST array overlap)."""
    if not keys:
        return []
    arr = "{" + ",".join('"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"' for k in keys) + "}"
    return supabase.select_all(TABLE, {"select": "id,status,batches,preview,file_name", "status": "eq.booked", "batches": f"ov.{arr}",
                                       "order": "uploaded_at.asc"})


def _delete_ai_images(paths: list[str]) -> None:
    """The AI page images (private bucket). Never fails the action that asked: a leftover picture is only storage."""
    if not paths:
        return
    try:
        supabase.storage_delete(list(paths), bucket=supabase.PRIVATE_BUCKET)
    except Exception:                                      # noqa: BLE001
        logger.exception("could not delete inbox AI page images %s", paths[:3])


def remove(file_id: str) -> dict:
    """ลบ: take back what it booked (only the keys it still owns), run the rebooks that asks for, delete the file, its AI page images
    and the row. Returns {"rebooked", "rebook_failed"}."""
    with _busy(file_id):
        row = _get(file_id)
        _refuse_running(row)
        res = _unbook(row) if row.get("status") == "booked" else {}
        supabase.storage_delete([row["storage_path"]], bucket=supabase.ACCOUNTING_BUCKET)
        _delete_ai_images(ai_image_paths(row.get("preview")))
        supabase.delete(TABLE, {"id": f"eq.{row['id']}"})
        n, failed = _run_rebooks(list(res.get("rebook") or []), {row["id"]})
    return {"rebooked": n, "rebook_failed": failed}


def list_files(month: str = "") -> dict:
    """The month's files plus everything not booked yet (any month), newest first."""
    import re
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise InboxError("เดือนไม่ถูกต้อง")
    params = {"select": "*", "order": "uploaded_at.desc"}
    if month:
        params["or"] = f"(month.eq.{month},status.neq.booked)"
    running = _running_ids()
    files = [row_view(r, running) for r in supabase.select_all(TABLE, params)]
    for v in files:
        if v["status"] == "ai_running":
            v["progress"] = progress_of(v["id"])
    return {"files": files, "ai_ready": ai_ready(),
            "readers": [getattr(r, "LABEL", getattr(r, "KIND", "")) for r in _readers() if not _is_scan(r)]}


def feed_files(month: str, branch: str = "") -> dict:
    """For the checklist (accounting.report): {feed: [dropped files]} of a company month."""
    rows = supabase.select_all(TABLE, {"select": "id,file_name,status,batches,preview,month", "month": f"eq.{month}", "order": "uploaded_at.asc"})
    return files_by_feed(rows, branch)


# ------------------------------------------------------------------ AI (phase 2): only on the owner's click, in the background
_jobs: dict = {}                 # file id -> {"done": n, "total": n}  (this server process)
_jobs_lock = threading.Lock()


def _running_ids() -> set:
    with _jobs_lock:
        return set(_jobs)


def ai_ready() -> bool:
    from app.config import settings
    return bool(settings.anthropic_api_key) and _optional("inbox_ai") is not None


def _spawn(fn: Callable, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True, name="inbox-ai").start()


def ai(file_id: str) -> dict:
    """อ่านด้วย AI: start the background read. The page asked the owner first (it shows how many pages)."""
    from app.config import settings
    if not settings.anthropic_api_key:
        raise InboxError("ยังไม่ได้ตั้งค่า AI บนเซิร์ฟเวอร์ (ANTHROPIC_API_KEY)")
    mod = _optional("inbox_ai")
    if mod is None:
        raise InboxError("ส่วนอ่านด้วย AI ยังไม่พร้อม")
    with _busy(file_id):
        row = _get(file_id)
        p = base_preview(row)
        if row.get("status") not in ("needs_ai", "failed", "ai_running") and not p.get("needs_ai"):
            raise InboxError("ไฟล์นี้อ่านได้แล้ว ไม่ต้องใช้ AI")
        with _jobs_lock:                                   # check and claim in one step: two clicks never start two reads
            if row["id"] in _jobs:
                raise InboxError("AI กำลังอ่านไฟล์นี้อยู่")
            _jobs[row["id"]] = {"done": 0, "total": p.get("pages") or 0}
        try:
            saved = _save(row["id"], {"status": "ai_running", "error": None})
        except Exception:
            with _jobs_lock:
                _jobs.pop(row["id"], None)
            raise
        _spawn(_ai_job, mod, row)
        return row_view(saved, _running_ids())


def _ai_job(mod, row: dict) -> None:
    fid = row["id"]

    def progress(done, total=None):
        with _jobs_lock:
            if fid in _jobs:
                _jobs[fid] = {"done": done, "total": total if total is not None else _jobs[fid].get("total")}
    try:
        data = _download(row)
        preview = _normal(mod.run(row, data, progress), None)
        values = file_values(preview.get("kind") or row.get("kind") or "scan", preview, None)
        values["ai_usage"] = preview.get("ai_usage")
        values["choices"] = {}
        _save(fid, values)
        new = set(ai_image_paths(preview))                 # a re-run: the pictures of the earlier read that this one did not rewrite
        _delete_ai_images([x for x in ai_image_paths(row.get("preview")) if x not in new])
    except Exception as exc:
        logger.exception("inbox AI read failed for %s", fid)
        try:
            failed = {"status": "failed", "error": _thai_error("AI อ่านไม่สำเร็จ", exc)}
            if getattr(exc, "usage", None):                 # a read that failed partway still spent tokens: keep the count (review, 26 Nov 2026)
                failed["ai_usage"] = exc.usage
            _save(fid, failed)
        except Exception:
            logger.exception("could not record the AI failure for %s", fid)
    finally:
        with _jobs_lock:
            _jobs.pop(fid, None)


def progress_of(file_id: str) -> Optional[dict]:
    with _jobs_lock:
        return dict(_jobs[file_id]) if file_id in _jobs else None


# ------------------------------------------------------------------ the checks (app/acc_checks.py)
def checks(month: str, branch: str = "") -> dict:
    import re
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise InboxError("เดือนไม่ถูกต้อง")
    mod = _optional("acc_checks")
    if mod is None:
        return {"available": False, "checks": [], "note": "ชุดตรวจความถูกต้องยังไม่พร้อม"}
    out = mod.run(month, branch) or {}
    return {"available": True, "checks": list(out.get("checks") or []), "note": out.get("note") or ""}
