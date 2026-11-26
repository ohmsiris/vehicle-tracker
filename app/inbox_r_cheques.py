"""
กล่องรับไฟล์ › ใบสรุปจ่ายเช็ค (HQ): the monthly cheque summary workbook HQ sends ("ใบสรุปจ่ายเช็คประจำเดือนMM-69-สระบุรี+แก่งคอย.xls").
Reader contract: app/inbox_common.py. The rows themselves are classified and turned into entries by app/acc_cheques.py (the same rules
the legacy importer scripts/import_hq_cheques.py used), so dropping the file here and the old script book exactly the same thing, under
the same batch keys ("cheque-YYYY-MM-<branch>"): re-dropping a month that the script booked replaces it, never adds to it.

ONE WORKBOOK, BOTH BRANCHES. One sheet per branch: the sheet whose name or title says แก่งคอย is Kaeng Khoi's, the other Saraburi's.
Each sheet is one batch.

WHICH MONTH (owner, 24 Nov 2026: "never trust one label"). The batch is named after the cheque RUN month (the run on the ~20th pays last
month's bills). HQ copies last month's workbook to start the new one and forgets to update parts of it: January's sheets still say
'09-68' at the end of their names, one file is named '03-69' but is May's. So every independent signal is read and weighed:
    title row Thai month ("ประจำเดือน พฤศจิกายน 2569")             3   HQ types it each month; the strongest single signal
    the dates on the cheques (median calendar month)               2   junk (1863, 2014) and stray dates > 60 days from the median dropped
    month tags in the row texts, plus one ("10-2569" -> Nov run)   2   the run pays last month's bills; the most common tag wins
    PEA bills the run pays (their bill period, plus one)           2   only when a bill is already stored (elec_bills)
    sheet-name suffix ("บจ.สระบุรี11-69")                         1   often stale
    the file name ("...ประจำเดือน11-69...")                       0.5 often stale
inbox_common.month_conflict() picks the month most weight agrees on; ANY signal that disagrees makes a conflict, and the engine waits for
the owner to pick (the preview is still built with the likely month so the owner can see what it would book).

EVERY ROW HAS A ROLE (the rule that stops double counting: one source books a cost, the others only pay or prove it):
    funding      money to petty cash (คชจ. + เบิกกลางเดือน, เงินเดือนสด ...): the costs come from the mid / end-of-month documents
    pays_bill    pays a PEA bill or a factory purchase bill the app already booked (Dr 2100), not a cost again
    transfer     lent / returned between branches or companies, ice bought from the other branch (settles 1600)
    owner        the owner's own payments (3200)
    tax          the accountant's row: fee + withholding tax + VAT remitted
    wages        a salary paid by cheque (7100 / 7110 / 7120)
    cost         salt, chemicals, bags, repairs ... (the cost itself)
    unknown      7900, the rules did not know it -- check
The total the sheet PRINTS ('รวม') is compared with what the rows add up to: HQ's SUM range sometimes misses a row (Apr 2026 Saraburi).
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Optional

from app import acc_cheques
from app.inbox_common import (Dropped, book_batches, empty_preview, entry_amount, month_conflict, month_label, shift_month, unbook_batches,
                              vote, workbook)

KIND = "cheques"
LABEL = "ใบสรุปจ่ายเช็ค (HQ)"
FEEDS = ("hq_cheques",)

W_TITLE, W_DATES, W_TAGS, W_PEA, W_SHEET, W_FILE = 3, 2, 2, 2, 1, 0.5
STRAY_DAYS = 60
ROLE_OF_KIND = {"funding": "funding", "pays_bill": "pays_bill", "transfer": "transfer", "settles_branch": "transfer", "owner": "owner",
                "accounting": "tax", "unknown": "unknown", "cost": "cost"}


# ------------------------------------------------------------------ sniff
def _is_pdf_copy(f: Dropped) -> bool:
    """HQ's PDF print of the same sheet (sent with the .xls): its text layer has the column heads and cheque rows, not the title."""
    t = f.text or ""
    return f.ext == ".pdf" and ("รายการจ่ายเช็ค" in t or ("ผู้รับเช็ค" in t and "คชจ.ตรง" in t))


def sniff(f: Dropped) -> float:
    """A workbook with HQ's title ("รายการจ่ายเช็ค ...") and cheque columns (ชื่อในเช็ค, เลขที่เช็ค). Content only; the name adds a little.
    HQ's PDF print of it is recognised too (kept, not booked: the .xls carries the same rows exactly)."""
    if _is_pdf_copy(f):
        return 0.7
    if not f.sheets:
        return 0.0
    best = 0.0
    for rows in f.sheets.values():
        text = " ".join(str(c) for r in rows[:5] for c in r if c not in (None, ""))
        s = (0.6 if "รายการจ่ายเช็ค" in text else 0.0) + (0.3 if "ชื่อในเช็ค" in text and "เลขที่เช็ค" in text else 0.0) \
            + (0.05 if "วันที่บนเช็ค" in text else 0.0)
        best = max(best, s)
    if best and "จ่ายเช็ค" in f.name:
        best += 0.05
    return round(min(best, 1.0), 2)


# ------------------------------------------------------------------ month signals (pure)
def _ym(mm: int, yy: int) -> Optional[str]:
    """('11', '69') or (11, 2569) -> '2026-11'."""
    if not 1 <= mm <= 12:
        return None
    y = yy - 543 if yy > 2400 else (2500 + yy - 543 if yy < 100 else yy)
    return f"{y}-{mm:02d}" if 2020 <= y <= 2040 else None


def sheet_suffix_month(name: str) -> Optional[str]:
    """'บจ.สระบุรี(สาขาแก่งคอย)11-69' -> '2026-11'."""
    m = re.search(r"(\d{1,2})\s*-\s*(\d{2})\s*$", name or "")
    return _ym(int(m.group(1)), int(m.group(2))) if m else None


def file_name_month(name: str) -> Optional[str]:
    """'11.ใบสรุปจ่ายเช็คประจำเดือน11-69-สระบุรี+แก่งคอย.xls' -> '2026-11' (the MM-YY after 'เดือน', else the first MM-YY)."""
    m = re.search(r"เดือน\s*(\d{1,2})\s*[-./]\s*(\d{2}|25\d\d)(?!\d)", name or "") or re.search(r"(?<!\d)(\d{1,2})-(\d{2})(?!\d)", name or "")
    return _ym(int(m.group(1)), int(m.group(2))) if m else None


def cheque_date_month(dates: list) -> Optional[str]:
    """The calendar month of the median cheque date, after dropping dates more than ~60 days from the median (a stale cheque from
    December in January's run, a date typed with the wrong year)."""
    ds = sorted(d for d in dates if d)
    if not ds:
        return None
    mid = ds[len(ds) // 2]
    keep = [d for d in ds if abs((d - mid).days) <= STRAY_DAYS] or ds
    mid = keep[len(keep) // 2]
    return f"{mid.year}-{mid.month:02d}"


def row_tag_month(rows: list[dict]) -> Optional[str]:
    """The most common month tag in the row texts ('ค่าไฟฟ้า 72/เดือน 10/69', 'ประกันสังคม 10/2569') plus one: the run pays last month's
    bills. Petty-cash funding rows are left out -- they name the month the cash is FOR (often the run month itself). A tie: no signal."""
    tags = Counter()
    for r in rows:
        if acc_cheques.FUNDING.search(r["what"]):
            continue
        m = acc_cheques.MONTH_TAG.search(r["what"])
        ym = _ym(int(m.group(1)), int(m.group(2))) if m else None
        if ym:
            tags[ym] += 1
    if not tags:
        return None
    top = tags.most_common(2)
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None
    return shift_month(top[0][0], 1)


def _merge(found: list[tuple], weight: float, source: str) -> list[dict]:
    """[(branch, value)] of one kind of signal over the sheets -> one signal when the sheets agree, one per sheet when they do not."""
    vals = [(b, v) for b, v in found if v]
    if not vals:
        return []
    if len({v for _, v in vals}) == 1:
        return [{"what": "month", "source": source, "value": vals[0][1], "weight": weight}]
    return [{"what": "month", "source": f"{source} ({b})", "value": v, "weight": weight / len(vals)} for b, v in vals]


# ------------------------------------------------------------------ read
def _sheets(f: Dropped) -> dict:
    try:
        return workbook(f.data, f.name)          # every row: Dropped.sheets keeps only the first 40
    except Exception:
        return f.sheets or {}


def _junk_dates(raw: list[list]) -> list[str]:
    """Item rows whose cheque-date cell holds something that is not a date (185000, 41653, '26/052569')."""
    out = []
    for r in raw:
        r = list(r) + [""] * (12 - len(r))
        try:
            no = int(float(r[0]))
        except (TypeError, ValueError):
            continue
        v = r[5]
        if v not in (None, "") and acc_cheques.be_date(v) is None:
            shown = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
            out.append(f"แถว {no}: วันที่บนเช็ค '{shown}' ไม่ใช่วันที่ — ไม่ใช้")
    return out


def _row_no(source_id: str) -> Optional[int]:
    m = re.search(r":r(\d+)[a-z]?$", source_id or "")
    return int(m.group(1)) if m else None


def _role(entry: dict) -> str:
    role = ROLE_OF_KIND.get(entry.get("kind"), "unknown")
    if role == "cost" and any(ln["account"] in ("7100", "7110", "7120") for ln in entry["lines"] if ln["debit"]):
        return "wages"
    return role


def _batch(sheet: dict, month: str, refs: tuple) -> tuple[dict, list[str]]:
    branch, rows = sheet["branch"], sheet["rows"]
    key = f"cheque-{month}-{branch}"
    built = acc_cheques.build(rows, branch=branch, file_month=date(int(month[:4]), int(month[5:7]), 1), batch=key,
                              bills=refs[0], purchases=refs[1])
    flags = sheet["junk"] + built["flags"]
    by_no: dict = {}
    for fl in flags:
        m = re.match(r"แถว (\d+):", fl)
        if m:
            by_no.setdefault(int(m.group(1)), []).append(fl.split(":", 1)[1].strip())
    out_rows, used = [], set()
    for e in built["entries"]:
        no = _row_no(e["source_id"])
        main = next((ln["account"] for ln in e["lines"] if ln["debit"]), None)
        out_rows.append({"date": e["entry_date"], "text": e["description"], "party": e["party"], "amount": entry_amount(e),
                         "role": _role(e), "account": main, "flag": "; ".join(by_no[no]) if no in by_no and no not in used else None,
                         "source_id": e["source_id"]})
        used.add(no)
    rows_total = round(sum(r["amount"] for r in rows), 2)
    file_total = sheet["file_total"]
    if file_total is not None and abs(file_total - rows_total) > 1.0:
        flags.append(f"ยอดรวมที่พิมพ์ในชีท {file_total:,.2f} ไม่ตรงกับผลรวมรายการ {rows_total:,.2f} (ต่าง {rows_total - file_total:,.2f}) "
                     f"— สูตรรวมของ HQ อาจไม่ครอบทุกแถว ลงตามรายการ")
    return ({"key": key, "feed": "hq_cheques", "source": "hq_cheques", "branch": branch, "month": month, "sheet": sheet["name"],
             "title": f"เช็คจาก HQ {branch} รอบ {month_label(month)}", "rows": out_rows, "file_total": file_total, "rows_total": rows_total,
             "entries": built["entries"], "matched": built["matched"]},
            [f"{branch} {fl}" for fl in flags])


def _read_pdf_copy(f: Dropped) -> dict:
    """Kept with its month, not booked: the same rows as the .xls, which is read exactly (a PDF's columns run into each other)."""
    p = empty_preview(KIND, LABEL, store_only=True)
    dates = [d for d in (acc_cheques.be_date(m) for m in re.findall(r"\d{1,2}/\d{1,2}/25\d\d", f.text or "")) if d]
    signals = [x for x in ({"what": "month", "source": "วันที่บนเช็ค", "value": cheque_date_month(dates), "weight": W_DATES},
                           {"what": "month", "source": "ชื่อไฟล์", "value": file_name_month(f.name), "weight": W_FILE}) if x["value"]]
    month, conflicts = month_conflict(signals, f.choices)
    p.update(month=month, signals=signals, conflicts=conflicts,
             note="PDF ของใบสรุปจ่ายเช็ค (HQ) — เก็บไว้ ไม่ลงบัญชี: ลงบัญชีจากไฟล์ .xls ที่มีรายการเดียวกัน (ถ้ายังไม่มี ขอไฟล์ .xls จาก HQ)")
    return p


def read(f: Dropped, refs: Optional[tuple] = None) -> dict:
    """refs = (PEA bills, purchase bills) as acc_cheques.load_refs() gives them -- read from the database when not passed."""
    if _is_pdf_copy(f):
        return _read_pdf_copy(f)
    p = empty_preview(KIND, LABEL)
    sheets = []
    for name, raw in _sheets(f).items():
        rows = acc_cheques.parse_sheet(raw)
        if not rows:
            continue
        sheets.append({"name": name, "branch": acc_cheques.sheet_branch(name, raw), "raw": raw, "rows": rows,
                       "file_total": acc_cheques.printed_total(raw), "junk": _junk_dates(raw)})
    if not sheets:
        p.update(store_only=True, note="ไม่พบรายการเช็คในไฟล์")
        return p
    seen, dup = set(), []
    for s in sheets:
        if s["branch"] in seen:
            dup.append(s["name"])
        seen.add(s["branch"])
    if dup:
        p["flags"].append(f"มีมากกว่าหนึ่งชีทของสาขาเดียวกัน ใช้ชีทแรก ไม่ใช้: {', '.join(dup)}")
        sheets = [s for i, s in enumerate(sheets) if s["branch"] not in {x["branch"] for x in sheets[:i]}]
    refs = refs if refs is not None else acc_cheques.load_refs()

    base = []
    base += _merge([(s["branch"], (lambda d: f"{d:%Y-%m}" if d else None)(acc_cheques.run_month(s["raw"]))) for s in sheets], W_TITLE, "หัวกระดาษ")
    base += _merge([(s["branch"], cheque_date_month([r["cheque_date"] for r in s["rows"]])) for s in sheets], W_DATES, "วันที่บนเช็ค")
    base += _merge([(s["branch"], row_tag_month(s["rows"])) for s in sheets], W_TAGS, "เดือนในรายการ +1")
    base += _merge([(s["branch"], sheet_suffix_month(s["name"])) for s in sheets], W_SHEET, "ท้ายชื่อชีท")
    fn = file_name_month(f.name)
    if fn:
        base.append({"what": "month", "source": "ชื่อไฟล์", "value": fn, "weight": W_FILE})
    provisional, _ = vote(base, "month")
    if not provisional and f.choices.get("month"):            # nothing in the file says the month: the owner picked it
        provisional = f.choices["month"]
    if not provisional:
        p.update(note="ไม่รู้เดือนของรอบเช็ค", signals=base,
                 conflicts=[{"key": "month", "message": "ไม่พบเดือนของรอบเช็คในไฟล์ — โปรดเลือก", "options": [], "default": None}])
        return p

    def build_all(month):
        out, flags = [], []
        for s in sheets:
            b, fl = _batch(s, month, refs)
            out.append(b)
            flags += fl
        return out, flags

    batches, flags = build_all(provisional)
    pea = sorted({shift_month(m["period"], 1) for b in batches for m in b["matched"] if m.get("pea") and m.get("period")})
    signals = base + [{"what": "month", "source": "ใบแจ้ง PEA ที่จ่าย +1", "value": v, "weight": W_PEA / len(pea)} for v in pea]
    month, conflicts = month_conflict(signals, f.choices)
    use = month or (conflicts[0]["default"] if conflicts else provisional)
    if use != provisional:
        batches, flags = build_all(use)
    signals += [{"what": "branch", "source": f"ชีท {s['name']}", "value": s["branch"]} for s in sheets]
    p.update(month=month, signals=signals, conflicts=conflicts, batches=batches, flags=p["flags"] + flags,
             note=f"รอบเช็ค {month_label(use)} · " + " · ".join(f"{b['branch']} {len(b['rows'])} แถว {b['rows_total']:,.2f} บาท" for b in batches))
    return p


# ------------------------------------------------------------------ book / unbook
def book(preview: dict, file_id: str) -> dict:
    """Replace each sheet's batch (cheque-YYYY-MM-<branch>): what the legacy importer or an earlier drop booked under the key goes first."""
    return book_batches(preview)


def unbook(preview: dict) -> None:
    unbook_batches(preview)
