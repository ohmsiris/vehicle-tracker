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
  file later can never delete what the new one booked. A reader may ask for "rebook" (a PEA bill stored after the cheque run that paid
  it: the cheque turns from a 5100 cost into "pays an existing bill"): every booked file of that kind and month is re-read and re-booked.
* reread: read again with the stored choices (a reader got better, or the employees list changed); a booked file is re-booked.
* remove: unbook (only the keys this file still owns), delete the stored file, delete the row.
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
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from app import inbox_common as ic
from app import supabase

logger = logging.getLogger("vehicle_tracker.inbox")

TABLE = "inbox_files"
MAX_FILES = 30                     # per request (the page sends big drops a few files at a time)
SCAN_READER = "inbox_r_scan"
TYPES = {  # extension -> media type; anything else is still accepted (the PEA AMR exports have no extension) as octet-stream
    ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".heic": "image/heic",
    ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12", ".csv": "text/csv", ".htm": "text/html", ".html": "text/html", ".txt": "text/plain",
}
STATUS_LABEL = {"read": "อ่านแล้ว รอลงบัญชี", "booked": "ลงบัญชีแล้ว", "needs_choice": "ต้องเลือกเดือน", "stored": "เก็บไว้ ไม่ลงบัญชี",
                "needs_ai": "อ่านด้วย AI ได้", "ai_running": "AI กำลังอ่าน...", "failed": "อ่านไม่สำเร็จ"}
_AI_STALE = "AI หยุดกลางคัน (เซิร์ฟเวอร์เริ่มใหม่) — กดอ่านด้วย AI อีกครั้ง"


class InboxError(ValueError):
    """User-fixable problem; str() is a Thai message safe to show (main.py maps it to 422)."""


class NotFound(InboxError):
    """No such dropped file (404)."""


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


def file_values(kind: str, preview: dict, error: Optional[str]) -> dict:
    """The inbox_files columns that follow from a read (status / month / branches / preview / error)."""
    branches = sorted({b.get("branch") for b in preview.get("batches") or [] if b.get("branch")})
    if not branches:
        branches = sorted({s["value"] for s in preview.get("signals") or [] if s.get("what") == "branch" and s.get("value")})[:2]
    month = preview.get("month")
    if not month and preview.get("store_only"):
        month = next((c.get("default") for c in preview.get("conflicts") or [] if c.get("key") == "month"), None)
    return {"kind": kind, "status": "failed" if error else status_of(preview), "month": month, "branches": branches,
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


def rebook_targets(requests: list[dict], rows: list[dict], exclude: set) -> list[dict]:
    """The booked files a booking asked to re-book ({"kind", "month"} each), not the ones already done in this round."""
    want = {(q.get("kind"), q.get("month")) for q in requests or [] if q.get("kind") and q.get("month")}
    seen, out = set(), []
    for r in rows:
        if r.get("status") == "booked" and (r.get("kind"), r.get("month")) in want and r["id"] not in exclude and r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out


def clean_choices(preview: dict, old: dict, new: dict) -> dict:
    """The owner's answers merged with earlier ones. An answer to an open conflict must be one of its options."""
    if not isinstance(new, dict) or not new:
        raise InboxError("ยังไม่ได้เลือก")
    open_ = {c.get("key"): c for c in preview.get("conflicts") or []}
    out = dict(old or {})
    for k, v in new.items():
        k, v = str(k), (None if v in (None, "") else str(v))
        c = open_.get(k)
        if c is not None and v is not None and v not in {str(o.get("value")) for o in c.get("options") or []}:
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
    p = row.get("preview") or {}
    keep = set(row.get("batches") or [])
    return {**p, "batches": [b for b in p.get("batches") or [] if b.get("key") in keep]}


def _num(x) -> Optional[float]:
    try:
        return None if x is None else round(float(x), 2)
    except (TypeError, ValueError):
        return None


def row_view(row: dict, running: Optional[set] = None) -> dict:
    """One dropped file as the list shows it (the full preview stays on the server until the owner opens it)."""
    p = row.get("preview") or {}
    batches = p.get("batches") or []
    status, error = row.get("status"), row.get("error")
    if status == "ai_running" and running is not None and row["id"] not in running:
        status, error = "failed", _AI_STALE         # the job died with the server: offer the button again
    ft = [_num(b.get("file_total")) for b in batches]
    rt = [_num(b.get("rows_total")) for b in batches]
    return {"id": row["id"], "file_name": row.get("file_name"), "kind": row.get("kind"), "label": p.get("label") or row.get("kind") or "ไฟล์",
            "status": status, "status_label": STATUS_LABEL.get(status, status), "month": row.get("month"), "branches": row.get("branches") or [],
            "uploaded_at": row.get("uploaded_at"), "uploaded_by": row.get("uploaded_by"), "booked_at": row.get("booked_at"),
            "booked_total": _num(row.get("booked_total")), "error": error, "ai_usage": row.get("ai_usage"), "note": p.get("note") or "",
            "pages": p.get("pages"), "needs_ai": bool(p.get("needs_ai")), "store_only": bool(p.get("store_only")),
            "conflicts": p.get("conflicts") or [], "choices": row.get("choices") or {}, "flags": len(p.get("flags") or []),
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
        if r.get("status") in ("failed", "ai_running", "needs_ai", "stored"):
            continue
        owned = set(r.get("batches") or [])
        for b in (r.get("preview") or {}).get("batches") or []:
            feed = b.get("feed")
            if not feed or (branch and b.get("branch") and b.get("branch") != branch):
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


def _get(file_id: str) -> dict:
    try:
        uuid.UUID(str(file_id))
    except ValueError:
        raise NotFound("ไม่พบไฟล์นี้")
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


def upload(files: list[tuple[str, bytes]], who: str = "") -> list[dict]:
    """Store, identify and read each dropped file. Returns one row per file (row_view) with a Thai "message"; a file already in the box
    returns its existing row (dedupe by sha256), an empty file is refused on its own without failing the others."""
    if not files:
        raise InboxError("เลือกไฟล์อย่างน้อยหนึ่งไฟล์")
    if len(files) > MAX_FILES:
        raise InboxError(f"ครั้งละไม่เกิน {MAX_FILES} ไฟล์")
    readers = _readers()
    out, seen = [], {}
    for name, data in files:
        name = fix_name(name or "file")[:200]
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


def get(file_id: str) -> dict:
    """One file with its full preview (the preview dialog) and the role names."""
    row = _get(file_id)
    return {**row_view(row, _running_ids()), "preview": row.get("preview") or {}, "roles": ic.ROLES}


def download(file_id: str) -> tuple[bytes, str, str]:
    row = _get(file_id)
    return _download(row), row.get("mime_type") or "application/octet-stream", row.get("file_name") or "file"


def choose(file_id: str, choices: dict) -> dict:
    """The owner's answer to a conflict (e.g. {"month": "2026-11"}): read again with it; a booked file is re-booked."""
    row = _get(file_id)
    if row.get("status") == "ai_running":
        raise InboxError("AI กำลังอ่านไฟล์นี้อยู่ — รอสักครู่")
    merged = clean_choices(row.get("preview") or {}, row.get("choices") or {}, choices)
    return _reprocess(row, merged, set())


def reread(file_id: str) -> dict:
    row = _get(file_id)
    if row.get("status") == "ai_running":
        raise InboxError("AI กำลังอ่านไฟล์นี้อยู่ — รอสักครู่")
    return _reprocess(row, row.get("choices") or {}, set())


def _ai_read(row: dict) -> bool:
    """Was this file's preview made by the AI? (Then re-reading does not run the AI again: choices are applied to what it read.)"""
    p = row.get("preview") or {}
    return bool(row.get("ai_usage") or p.get("ai_usage")) and not p.get("needs_ai")


def _reprocess(row: dict, choices: dict, visited: set) -> dict:
    was_booked = row.get("status") == "booked"
    if _ai_read(row):
        ai = _optional("inbox_ai")
        fn = getattr(ai, "apply_choices", None) if ai else None
        preview = _normal(fn(row.get("preview") or {}, choices) if fn else apply_choices(row.get("preview") or {}, choices), None)
        kind, error = row.get("kind") or preview.get("kind"), None
    else:
        kind, preview, error = read_file(row["file_name"], _download(row), choices)
    if error and was_booked:                               # keep what is booked rather than lose it to a failed read
        raise InboxError(f"{error} — ของที่ลงบัญชีไว้ยังอยู่เหมือนเดิม")
    if was_booked:
        _unbook(row)
    values = {**file_values(kind, preview, error), "choices": choices, "ai_usage": row.get("ai_usage") or preview.get("ai_usage")}
    if was_booked:
        values.update(batches=[], booked_total=None, booked_at=None)
    saved = _save(row["id"], values)
    if was_booked and saved.get("status") == "read":
        return book(row["id"], visited)
    return row_view(saved, _running_ids())


def _unbook(row: dict) -> None:
    """Remove what this file booked (only the keys it still owns)."""
    if not row.get("batches"):
        return
    reader = reader_for(row.get("kind"))
    fn = getattr(reader, "unbook", None) if reader else None
    (fn or ic.unbook_batches)(owned_preview(row))


def book(file_id: str, visited: Optional[set] = None) -> dict:
    """ลงบัญชี: the reader books the preview (replacing by batch key), the row records what it booked; then the rebooks it asked for."""
    visited = set() if visited is None else visited
    visited.add(file_id)
    row = _get(file_id)
    p = row.get("preview") or {}
    status = row.get("status")
    if p.get("conflicts") or status == "needs_choice":
        raise InboxError("ยังมีเรื่องที่ต้องเลือกก่อน (" + " · ".join(c.get("message", "") for c in p.get("conflicts") or []) + ")")
    if status == "stored" or p.get("store_only"):
        raise InboxError("ไฟล์นี้เก็บไว้เฉย ๆ ไม่ลงบัญชี" + (f" — {p['note']}" if p.get("note") else ""))
    if status in ("needs_ai", "ai_running") or p.get("needs_ai"):
        raise InboxError("ไฟล์นี้ยังไม่ได้อ่าน — กดอ่านด้วย AI ก่อน")
    if status == "failed":
        raise InboxError("ไฟล์นี้อ่านไม่สำเร็จ — กดอ่านใหม่")
    if not p.get("batches"):
        raise InboxError("ไม่มีรายการให้ลงบัญชีในไฟล์นี้")
    reader = reader_for(row.get("kind"))
    if reader is None and not _ai_read(row):
        raise InboxError("ยังไม่มีตัวลงบัญชีของไฟล์ประเภทนี้ในระบบ")
    if status == "booked":                                 # booking again: first take back what it booked (keys may have changed)
        _unbook(row)
    res = (reader.book(p, file_id) if reader is not None else ic.book_batches(p)) or {}
    keys = list(res.get("batches") or [b["key"] for b in p.get("batches") or []])
    for other_id, keep in superseded(_rows_with_keys(keys), keys, file_id):
        _save(other_id, {"batches": keep, **({} if keep else {"status": "read", "booked_at": None, "booked_total": None,
                                                              "error": f"ถูกแทนที่ด้วย {row.get('file_name')}"})})
    saved = _save(file_id, {"status": "booked", "batches": keys, "booked_total": _num(res.get("booked")), "booked_at": _now(), "error": None})
    rebooked = 0
    if res.get("rebook"):
        months = sorted({q.get("month") for q in res["rebook"] if q.get("month")})
        cands = _booked_rows(months)
        for r in rebook_targets(res["rebook"], cands, visited):
            visited.add(r["id"])
            try:
                _reprocess(r, r.get("choices") or {}, visited)
                rebooked += 1
            except InboxError:
                logger.warning("rebook of %s refused", r["id"])
    return {**row_view(saved, _running_ids()), "rebooked": rebooked}


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
    return supabase.select_all(TABLE, {"select": "id,status,batches", "status": "eq.booked", "batches": f"ov.{arr}", "order": "uploaded_at.asc"})


def remove(file_id: str) -> None:
    row = _get(file_id)
    if row.get("status") == "ai_running" and row["id"] in _running_ids():
        raise InboxError("AI กำลังอ่านไฟล์นี้อยู่ — รอให้เสร็จก่อนจึงลบได้")
    if row.get("status") == "booked":
        _unbook(row)
    supabase.storage_delete([row["storage_path"]], bucket=supabase.ACCOUNTING_BUCKET)
    supabase.delete(TABLE, {"id": f"eq.{file_id}"})


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
    row = _get(file_id)
    if row["id"] in _running_ids():
        raise InboxError("AI กำลังอ่านไฟล์นี้อยู่")
    p = row.get("preview") or {}
    if row.get("status") not in ("needs_ai", "failed", "ai_running") and not p.get("needs_ai"):
        raise InboxError("ไฟล์นี้อ่านได้แล้ว ไม่ต้องใช้ AI")
    with _jobs_lock:
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
    except Exception as exc:
        logger.exception("inbox AI read failed for %s", fid)
        try:
            _save(fid, {"status": "failed", "error": _thai_error("AI อ่านไม่สำเร็จ", exc)})
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
