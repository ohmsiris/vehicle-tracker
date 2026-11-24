"""
บัญชี › ลงข้อมูล › กล่องรับไฟล์ (the drop box; owner, 24 Nov 2026): drop ANY monthly document, the app works out what it is from what is
INSIDE it (never the file name alone), reads it with an exact reader, shows a preview, and books it only when the owner presses ลงบัญชี.
Re-dropping a file replaces what it booked; nothing is ever counted twice. This module is the CONTRACT every reader follows plus the
helpers they share. The engine (storage, the inbox_files table, endpoints, background AI jobs) is app/inbox.py.

READER MODULES (app/inbox_r_*.py, listed in READERS below). Each one exposes:

    KIND  = "cheques"                      # unique key, stored in inbox_files.kind
    LABEL = "ใบสรุปจ่ายเช็ค (HQ)"          # what the owner sees
    FEEDS = ("hq_cheques",)                # the checklist rows (accounting.FEEDS keys) it fills

    def sniff(f: Dropped) -> float         # 0..1: how sure this reader is that the file is its kind. Content only (f.text / f.sheets /
                                           # f.is_scan ...); the name may add a little, never decide. Must be fast and never touch the DB.
    def read(f: Dropped) -> dict           # the PREVIEW (below). May READ the database (employees, elec_bills, vehicles ...), never writes.
                                           # Honours f.choices (the owner's answer to a conflict, e.g. {"month": "2026-11"}).
    def book(preview: dict, file_id: str) -> dict
                                           # writes. FIRST deletes everything an earlier booking of the same batch keys made (replace,
                                           # never add), then inserts. Returns {"batches": [keys], "booked": float, "rebook": [...]}.
                                           # "rebook": [{"kind": "cheques", "month": "2026-11"}] asks the engine to re-read and re-book
                                           # already-booked files of that kind and month (e.g. a PEA bill stored after the cheque run
                                           # that paid it: the cheque must turn from a 5100 cost into "pays an existing bill").
    def unbook(preview: dict) -> None      # OPTIONAL. Default (unbook_batches): delete acc_entries whose source_id == key or like key:*.
                                           # A reader with side tables (elec_bills, employee_pay_months, sales_lines, acc_docs) defines it.

PREVIEW (a plain dict, stored as jsonb in inbox_files.preview):

    {"kind": KIND, "label": LABEL,
     "store_only": False,                  # True = recognised, kept with the month, NOT booked (a payslip, the Bangchak PDF that copies the
                                           # xlsx already read, a cover letter ...). "note" says why.
     "note": "",
     "needs_ai": False,                    # True = only AI can read it (scan / unknown); "pages" says how many pages it would read.
     "pages": None,
     "month": "2026-11",                   # the company month (26th-25th, named after the 25th) the file belongs to, or None
     "signals": [{"what": "month", "source": "หัวกระดาษ", "value": "2026-11"}, ...],   # every independent signal seen (month / branch)
     "conflicts": [{"key": "month", "message": "หัวกระดาษบอก ต.ค. 2569 แต่วันที่บนเช็คเป็น พ.ย. 2569",
                    "options": [{"value": "2026-11", "label": "พ.ย. 2569 (วันที่บนเช็ค)"}, ...], "default": "2026-11"}],
                                           # ANY disagreement between signals is a conflict: the engine will not book until the owner picks.
     "batches": [{"key": "cheque-2026-11-สระบุรี",            # replace key: entries' source_id == key or starts with key + ":"
                  "feed": "hq_cheques", "source": "hq_cheques", "branch": "สระบุรี", "month": "2026-11", "title": "...",
                  "rows": [{"date": "2026-10-25", "text": "...", "party": "...", "amount": 1234.5, "role": "cost",
                            "account": "5300", "flag": None}],
                  "file_total": 845000.0,                       # the total PRINTED in the file (None if it has none)
                  "rows_total": 845000.0,                       # what the rows add up to
                  "entries": [...]}],                           # exactly what accounting._insert_entries takes
     "flags": ["แถว 8: ..."],
     "ai_usage": None}                     # set by the AI readers: {"model", "calls", "input_tokens", "output_tokens"}

ROLES (rows[].role): see ROLES below. Every row says what it does to the books, so the owner can see at a glance that a cheque row that
tops up petty cash is NOT a cost (the papers are) -- the rule that stops double counting: one source books a cost, the others only
pay or prove it.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

BRANCHES = ("สระบุรี", "แก่งคอย")
READERS = ("inbox_r_pea", "inbox_r_cheques", "inbox_r_payroll", "inbox_r_fuel", "inbox_r_sales", "inbox_r_scan")   # app.<name>; missing ones are skipped
MIN_SNIFF = 0.5                    # below this, no exact reader claims the file: it goes to inbox_r_scan (AI, if the owner asks)

ROLES = {
    "cost": "ค่าใช้จ่าย",
    "wages": "เงินเดือน / ค่าแรง",
    "revenue": "รายได้",
    "funding": "เติมเงินสดย่อย (ไม่ใช่ค่าใช้จ่าย)",
    "pays_bill": "จ่ายบิลที่มีอยู่แล้ว (ไม่ใช่ค่าใช้จ่ายซ้ำ)",
    "advance": "เงินเบิกล่วงหน้า (ไม่ใช่ค่าใช้จ่าย)",
    "transfer": "โอน / ยืมระหว่างสาขา (ไม่ใช่ค่าใช้จ่าย)",
    "owner": "ใช้ส่วนตัวเจ้าของ (ไม่ใช่ค่าใช้จ่าย)",
    "tax": "ภาษี / ค่าทำบัญชี",
    "unknown": "ยังไม่รู้หมวด (7900 — ควรตรวจ)",
    "info": "ข้อมูลประกอบ (ไม่ลงบัญชี)",
}
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
THAI_SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


@dataclass
class Dropped:
    """One dropped file as the readers see it. Built once by sniff_file(); `choices` carries the owner's answers to conflicts."""
    name: str
    data: bytes
    ext: str = ""
    text: str = ""                            # the text layer of the first pages of a PDF ('' for a scan or a non-PDF)
    pages: int = 0                            # PDF pages (0 for other files)
    is_scan: bool = False                     # a PDF with no text layer, or a photo
    sheets: Optional[dict] = None             # workbook: {sheet name: first 40 rows}; None otherwise
    choices: dict = field(default_factory=dict)


# ------------------------------------------------------------------ building a Dropped
def sniff_file(name: str, data: bytes, choices: Optional[dict] = None) -> Dropped:
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    f = Dropped(name=name, data=data, ext=ext, choices=dict(choices or {}))
    if ext == ".pdf" or data[:5] == b"%PDF-":
        f.ext = ".pdf"
        f.text, f.pages = pdf_text(data, max_pages=3)
        f.is_scan = len(re.sub(r"\s+", "", f.text)) < 40
    elif ext in (".jpg", ".jpeg", ".png", ".webp", ".heic") or data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
        f.is_scan = True
    elif ext in (".xls", ".xlsx", ".xlsm", ".csv"):
        try:
            f.sheets = workbook(data, name, max_rows=40)          # only the top: a 20k-row export must not slow the upload
        except Exception:                     # a .xls that is really HTML (PEA AMR export) or a broken file: readers look at the bytes
            f.sheets = None
    elif ext in (".htm", ".html"):
        f.text = data[:20000].decode("utf-8", errors="replace")
    return f


def pdf_text(data: bytes, max_pages: int = 3) -> tuple[str, int]:
    """(text of the first max_pages pages, page count). Thai sara-am split off by the PDF font is put back (fix_thai)."""
    import pymupdf
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return "", 0
    with doc:
        text = "\n".join(doc[i].get_text() for i in range(min(max_pages, doc.page_count)))
        return fix_thai_text(text), doc.page_count


def pdf_page_images(data: bytes, dpi: int = 150) -> list[bytes]:
    """Every page of a PDF as a JPEG (for the AI readers; a 29-page scan is read page by page)."""
    import pymupdf
    out = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            out.append(page.get_pixmap(dpi=dpi).tobytes("jpeg"))
    return out


def workbook(data: bytes, name: str = "", max_rows: Optional[int] = None) -> dict:
    """{sheet name: rows (lists of cell values)} of an .xls / .xlsx / .csv given as BYTES (only the first max_rows rows of each sheet if given)."""
    low = name.lower()
    if low.endswith(".csv"):
        import csv
        text = data.decode("utf-8-sig", errors="replace")
        rows = []
        for row in csv.reader(io.StringIO(text)):
            if max_rows and len(rows) >= max_rows:
                break
            rows.append(row)
        return {"csv": rows}
    if low.endswith(".xls") or data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        import xlrd
        book = xlrd.open_workbook(file_contents=data, on_demand=True)
        return {sh.name: [[c.value for c in sh.row(i)] for i in range(min(sh.nrows, max_rows or sh.nrows))] for sh in book.sheets()}
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = {ws.title: [list(r) for r in ws.iter_rows(values_only=True, max_row=max_rows)] for ws in wb.worksheets}
    wb.close()
    return out


_MARKS = "ัิ-ฺ็-๎"


def fix_thai_text(text: str) -> str:
    """Line-preserving version of payroll_people_drivers.fix_thai: 'ก ำ' -> 'กำ' and stray spaces before Thai marks."""
    text = re.sub(rf"[ \t]+([{_MARKS}]*)[าำ](?=\S)", lambda m: m.group(1) + "ำ", text)
    return re.sub(rf"[ \t]+([{_MARKS}])", r"\1", text)


# ------------------------------------------------------------------ months
def company_month(d: date) -> str:
    """The company month a day falls in: 26 Oct - 25 Nov = "2026-11"."""
    if d.day >= 26:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return f"{y}-{m:02d}"
    return f"{d.year}-{d.month:02d}"


def shift_month(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:7]) - 1 + n
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def month_label(month: Optional[str]) -> str:
    """'2026-11' -> 'พ.ย. 2569'."""
    if not month:
        return "-"
    return f"{THAI_SHORT[int(month[5:7]) - 1]} {int(month[:4]) + 543}"


def thai_month_in(text: str) -> Optional[str]:
    """The first 'พฤศจิกายน 2569' / 'พ.ย. 69' / 'พ.ย.2569' in the text -> '2026-11' (a calendar month: the caller decides what it means)."""
    for i, full in enumerate(THAI_MONTHS):
        m = re.search(full + r"\s*(?:พ\.?ศ\.?\s*)?(25\d\d|\d\d)\b", text or "")
        if m:
            return f"{_be_year(m.group(1))}-{i + 1:02d}"
    for i, short in enumerate(THAI_SHORT):
        m = re.search(re.escape(short) + r"\s*(25\d\d|\d\d)\b", text or "")
        if m:
            return f"{_be_year(m.group(1))}-{i + 1:02d}"
    return None


def _be_year(s: str) -> int:
    y = int(s)
    return (y if y > 2400 else 2500 + y) - 543


def be_date(text: str) -> Optional[date]:
    """'30/10/2569', '30/10/69', '30-10-69' -> a date (None outside 2020-2040: junk in a date column is not a date)."""
    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b", str(text or ""))
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y = y - 543 if y > 2400 else (2500 + y - 543 if y < 100 else y)
    try:
        out = date(y, mo, d)
    except ValueError:
        return None
    return out if date(2020, 1, 1) <= out <= date(2040, 12, 31) else None


def vote(signals: list[dict], what: str) -> tuple[Optional[str], list[dict]]:
    """(the value most signals agree on, the signals that disagree with it). Signals: {"what", "source", "value", "weight"?}."""
    mine = [s for s in signals if s.get("what") == what and s.get("value")]
    if not mine:
        return None, []
    score: dict = {}
    for s in mine:
        score[s["value"]] = score.get(s["value"], 0) + float(s.get("weight", 1))
    best = max(score.items(), key=lambda kv: kv[1])[0]
    return best, [s for s in mine if s["value"] != best]


def month_conflict(signals: list[dict], choices: dict, labels: Optional[dict] = None) -> tuple[Optional[str], list[dict]]:
    """The month to use and the conflict to show (if the signals disagree and the owner has not chosen yet)."""
    if choices.get("month"):
        return choices["month"], []
    best, against = vote(signals, "month")
    if not against:
        return best, []
    values = [best] + [s["value"] for s in against if s["value"] != best]
    seen, opts = set(), []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        srcs = ", ".join(s["source"] for s in signals if s.get("what") == "month" and s.get("value") == v)
        opts.append({"value": v, "label": f"{month_label(v)} ({srcs})"})
    msg = " แต่ ".join(f"{', '.join(s['source'] for s in signals if s.get('what') == 'month' and s.get('value') == v)} บอก {month_label(v)}" for v in seen)
    return None, [{"key": "month", "message": f"เดือนไม่ตรงกัน: {msg}", "options": opts, "default": best}]


# ------------------------------------------------------------------ preview / booking helpers
def empty_preview(kind: str, label: str, **kw) -> dict:
    out = {"kind": kind, "label": label, "store_only": False, "note": "", "needs_ai": False, "pages": None, "month": None,
           "signals": [], "conflicts": [], "batches": [], "flags": [], "ai_usage": None}
    out.update(kw)
    return out


def role_of_entry(entry: dict) -> str:
    """A role for an entry whose builder did not give one (from its accounts)."""
    if entry.get("kind") in ROLES:
        return entry["kind"]
    accounts = {ln["account"] for ln in entry.get("lines", []) if ln.get("debit")}
    if "1100" in accounts:
        return "funding"
    if "2100" in accounts:
        return "pays_bill"
    if "1500" in accounts:
        return "advance"
    if "1600" in accounts:
        return "transfer"
    if "3200" in accounts:
        return "owner"
    if "7900" in accounts:
        return "unknown"
    if any(a.startswith("4") for ln in entry.get("lines", []) if ln.get("credit") for a in [ln["account"]]):
        return "revenue"
    return "cost"


def entry_amount(entry: dict) -> float:
    return round(sum(float(ln.get("debit") or 0) for ln in entry.get("lines", [])), 2)


def unbook_batches(preview: dict) -> None:
    """Default unbook: every acc_entries row of the preview's batch keys (source_id == key or key:*)."""
    from app import supabase
    for b in preview.get("batches") or []:
        key = b["key"]
        supabase.delete("acc_entries", {"source_id": f"eq.{key}"})
        supabase.delete("acc_entries", {"source_id": f"like.{key}:*"})


def book_batches(preview: dict) -> dict:
    """Default book for an entries-only reader: replace each batch (delete by key, insert its entries)."""
    from app.accounting import _insert_entries, _require
    _require()
    unbook_batches(preview)
    total = 0.0
    for b in preview.get("batches") or []:
        _insert_entries(b.get("entries") or [])
        total += sum(entry_amount(e) for e in b.get("entries") or [])
    return {"batches": [b["key"] for b in preview.get("batches") or []], "booked": round(total, 2), "rebook": []}


def close(a: Optional[float], b: Optional[float], tol: float = 1.0) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol
