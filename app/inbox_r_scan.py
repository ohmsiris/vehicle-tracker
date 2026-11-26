"""
กล่องรับไฟล์ › reader "scan": the fallback for every file no exact reader claims (owner, 24 Nov 2026).

* sniff: 0.3 for a scan (a PDF with no text layer, a photo), 0.05 for anything else -- below inbox_common.MIN_SNIFF, so any exact reader
  that recognises the file wins; this one only catches what is left.
* read: NO API call. It says how many pages the AI would read (the owner sees 'อ่านด้วย AI (N หน้า)' before anything is spent) and
  what the file NAME suggests -- marked as a hint, because names are often stale (owner: HQ forgets to update titles).
  If the same bytes were already read by AI (inbox_files.preview of the same sha256 has the page reads), read() rebuilds that preview
  with the owner's choices instead -- so answering a conflict on an AI preview never pays for a second read.
* book / unbook: for previews made by app/inbox_ai.py, keyed on what the preview holds:
    - an expense document (preview["acc_docs_batch"]): through app/acc_docs (pages + entries + the review screen), replacing that
      half's batch and, when the owner chose "replace", the pages an older import of the same half wrote;
    - single bills: inbox_common.book_batches (entries only), asking the engine to re-book the cheque run of the NEXT month (the HQ
      run on the 20th pays last month's bills: shift_month(month, 1)) so the cheque that pays this bill turns into "pays an existing
      bill" (app/acc_cheques matches open 'bill:' entries by amount and party, or by the bill number in the cheque's note). unbook asks
      for the same rebook, so removing the bill turns that cheque back into the cost.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from app import inbox_ai
from app import inbox_common as ic

KIND = "scan"
LABEL = "เอกสารสแกน / ไฟล์ที่ต้องให้ AI อ่าน"
FEEDS = ("midmonth", "endmonth", "water_irrigation", "water_city")


def sniff(f: ic.Dropped) -> float:
    return 0.3 if f.is_scan else 0.05


def _earlier_ai(f: ic.Dropped):
    """The AI preview stored for these exact bytes, if any (read only; no database -> None)."""
    try:
        from app import supabase
        if not supabase.configured():
            return None
        rows = supabase.select("inbox_files", {"select": "preview", "sha256": f"eq.{hashlib.sha256(f.data).hexdigest()}", "limit": "1"})
    except Exception:                                  # noqa: BLE001 -- inbox_files not there yet: treat as never read
        return None
    prev = (rows[0].get("preview") if rows else None) or None
    return prev if isinstance(prev, dict) and prev.get("ai_pages") else None


def read(f: ic.Dropped) -> dict:
    prev = _earlier_ai(f)
    if prev:
        return inbox_ai.apply_choices(prev, f.choices)
    pages = f.pages if f.ext == ".pdf" else inbox_ai.page_count(f.name, f.data)
    h = inbox_ai.name_hints(f.name)
    hint = inbox_ai.hint_text(h)
    signals = [{"what": w, "source": "ชื่อไฟล์ (คำใบ้)", "value": h[w], "weight": 0.5, "hint": True} for w in ("month", "branch", "half") if h.get(w)]
    if not pages:
        return ic.empty_preview(KIND, LABEL, store_only=True, signals=signals, hint=hint,
                                note="ไม่รู้จักไฟล์นี้ และ AI อ่านได้เฉพาะ PDF หรือรูปภาพ — เก็บไว้กับเดือน ไม่ลงบัญชี")
    what = "ไฟล์สแกน / ลายมือ" if f.is_scan else "ไฟล์ที่ไม่มีตัวอ่านรู้จัก"
    return ic.empty_preview(KIND, LABEL, needs_ai=True, pages=pages, signals=signals, hint=hint,
                            note=f"{what} {pages} หน้า — อ่านได้ด้วย AI เท่านั้น กด 'อ่านด้วย AI ({pages} หน้า)' เมื่อต้องการ (ยังไม่เสียค่าใช้จ่าย)"
                                 + (f" · {hint}" if hint else ""))


def book(preview: dict, file_id: str) -> dict:
    from app import acc_docs
    if preview.get("needs_ai"):
        raise ValueError("ยังไม่ได้อ่านด้วย AI — กด 'อ่านด้วย AI' ก่อน")
    if preview.get("conflicts"):
        raise ValueError("ยังมีเรื่องที่ต้องเลือก (เดือน / สาขา / ครึ่งเดือน) ก่อนลงบัญชี")
    if preview.get("store_only") or not preview.get("batches"):
        return {"batches": [], "booked": 0.0, "rebook": []}
    batch = preview.get("acc_docs_batch")
    if batch:
        try:
            acc_docs.import_batch(batch, images=preview.get("ai_images") or {}, replace_docs=preview.get("replace_docs"))
        except acc_docs.BadBatch as exc:
            if "แก้บนหน้าตรวจ" in str(exc):
                raise acc_docs.BadBatch("ครึ่งเดือนนี้มีหน้าที่แก้บนหน้าตรวจแล้ว — ลงใหม่จะทับที่แก้ไว้ (ลบไฟล์เดิมออกก่อน ถ้าตั้งใจ)") from exc
            raise
        b = preview["batches"][0]
        return {"batches": [batch["batch"]], "booked": round(sum(ic.entry_amount(e) for e in b.get("entries") or []), 2), "rebook": []}
    out = ic.book_batches(preview)
    out["rebook"] = _cheque_rebook(preview)
    return out


def _cheque_rebook(preview: dict) -> list[dict]:
    """The cheque run that would pay these bills: next month's (review 26 Nov 2026: the bill month itself never holds that cheque)."""
    months = sorted({b.get("month") or preview.get("month") for b in preview.get("batches") or []
                     if b.get("entries") and str(b.get("key") or "").startswith("bill:")} - {None})
    return [{"kind": "cheques", "month": ic.shift_month(m, 1)} for m in months]


def unbook(preview: dict) -> Optional[dict]:
    """Undo what book() wrote. Pages of an OLDER import this file replaced are not brought back (re-run that import if needed).
    A bill's removal asks for the same cheque rebook as its booking: the cheque that paid it becomes the cost again."""
    from app import acc_docs
    batch = preview.get("acc_docs_batch")
    if batch:
        acc_docs.remove_batch(batch["batch"])
        return None
    ic.unbook_batches(preview)
    rebook = _cheque_rebook(preview)
    return {"rebook": rebook} if rebook else None
