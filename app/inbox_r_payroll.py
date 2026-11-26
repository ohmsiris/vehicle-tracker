"""
กล่องรับไฟล์ › เงินเดือน (owner, 24 Nov 2026): HQ's monthly payroll files, dropped as they come -- the summary PDF of each group
(ใบสรุป / สรุปเงินเดือน), the xlsx HQ sometimes sends instead, and the papers that come with them (รับสด list, สลิป, ใบเซ็นรับสลิป).

Three groups, one summary each per month:
    FAC  ช่าง + ออฟฟิศ + ฝ่ายผลิต สระบุรี      codes SB-FR...   wages 5400
    DRV  พขร. (จัดส่ง) สระบุรี                 codes SB-PD...   wages 6200
    KK   แก่งคอย (everyone)                     codes SB-KK...   wages 5400

WHAT THE FILE IS, FROM ITS CONTENT (never the name alone; the owner, 24 Nov 2026: "HQ forgets to update titles"):
  * a SUMMARY has the deductions box at the end ("สรุปรายหัก คืนบัญชี" in Saraburi, "รายการหัก" in Kaeng Khoi) under the per-person
    table. That box is what makes it the summary -- not the title: the drivers' summaries of Sep and Oct 2569 are titled
    "ใบเซ็นต์รับสลิปจัดส่ง" (the sign-for-slip sheet's title, left over), and the factory summaries since Apr 2569 are titled
    "รับเงินสด ฝ่ายผลิต" (the cash list's). Both are flagged, then read as the summary they are.
  * the xlsx workbook is the same table with formulas: its main sheet (ตารางรายรับ-จ่ายพนง. / สรุป) has the SB- people and the
    รวมรับ / รวมจ่าย / คงเหลือ headers. It is a summary too, booked under the SAME key as the PDF (so dropping both never doubles).
  * a slip file ("สลิปเงินเดือน ... ประจำเดือน", one slip per person), the cash list ("รายชื่อรับเงินสด", "รวมสด เฉพาะค่าแรง") and the
    sign sheet ("เซ็นรับสลิป", "โอนเข้าบัญชี") are the same people as the summary, already counted by it: recognised and kept with the
    month (store_only), never booked.

WHICH GROUP / BRANCH: the title words (แก่งคอย / ช่าง+ออฟฟิศ+ฝ่ายผลิต / จัดส่ง, พขร) AND the employee-code prefixes (SB-FR / SB-PD /
SB-KK, the majority of every code in the file) AND the file name (a little). The branch comes from the PEOPLE: their codes are looked up
in the employees table (read only) and the majority branch is taken; the title must agree (a "สาขาแก่งคอย" title with Saraburi people is
a conflict for the owner, not a guess).

WHICH MONTH (the company month, 26th-25th, named after the 25th -- HQ's "ตุลาคม 2569" payroll is the 26 Sep - 25 Oct month):
    the Thai month + year in the title (weight 3; "พฤภาคม 2569", HQ's typo in the May drivers' sheet, is read as May),
    the half-month period printed in the body ("คืนสำรองค่าใช้จ่าย11-25/10/2569", "ค่าใช้จ่าย 11-25/10/69"; weight 2),
    the print date at the top of a Saraburi summary ("01/11/2569" = printed after the 25th of October -> October; weight 1),
    the file name (weight 0.5).
  On the papers kept with the month (slip / cash list / sign sheet) the title weighs only 0.75 and a slip's date is its pay date: the
  drivers' slips of Aug (corrected) and Sep 2569 are headed "ประจำเดือน : กรกฎาคม 2569" but paid 01/09 and 01/10/2569. A stored paper
  files under the month the other signals give, and the disagreement is a flag (a stored file asks no question).
  Any disagreement is a conflict (month_conflict): the owner picks. And if the same group already has a month booked with exactly these
  totals, the file is most likely that month's file again under a stale name: a "duplicate" conflict, default "keep, do not book".

WHAT IS BOOKED (one batch, key "payroll:YYYY-MM:GRP" -- the same source_id scripts/import_payroll.py has always written, so a drop
replaces the imported entry instead of adding a second one):
  * the ledger entry acc_payroll.build_entry makes (Dr wages gross less the petty-cash pay-back, Cr the 8 deductions, Cr 1100 net);
  * employee_pay_months for exactly (month, group) -- never the whole month: the other two groups' rows stay as they are.
  Totals come from the PRINTED total row (the PDF's numbers are exact); the per-person rows (payroll_people_*) must add up to it.
  The deduction split comes from the printed box; when a sheet's box cannot be read (Jan 2569 Saraburi's older layout, Aug 2569
  Kaeng Khoi's first issue) the people's own deduction columns are summed instead -- they add up to รวมจ่าย, which is checked. (For Jan
  2569 that gives exactly the split scripts/import_payroll.py hard-codes in PARTS.) A workbook is booked in whole baht, half up, as its
  printed PDF shows it: dropping the xlsx or the PDF of a month books the same numbers (checked on every Jan-Oct 2569 file, 24 Nov 2026).
  A baht lost to rounding between the printed totals goes to the net-paid line, flagged.
  Checks in every batch ("checks", each {what, ok, detail}; a failed one is also a flag): people + pay-back = printed รวมรับ, people's
  deductions = รวมจ่าย, people's net = คงเหลือ, no code twice, the box = the people's columns account by account, gross - deductions =
  net, the 8 deductions = รวมจ่าย, the entry balances, and the wages line = gross less the pay-back (the pay-back is never wages).

ROWS (what the owner sees before ลงบัญชี):
  role "wages"   one row per person, their gross (รวมรับ)
  role "funding" the petty-cash pay-back rows with no employee ("คืนสำรองค่าใช้จ่าย 11-25/..", "ค่าน้ำมัน จ่ายสด11-25/.."): money the
                 sheet pays back to petty cash for the end-of-month papers, already booked from those papers -- NOT wages
                 (build_entry takes them out of both gross and net).
                 people + funding = the printed รวมรับ = file_total = rows_total.
  role "advance" / "info"  the deductions split (หัก เบิกล่วงหน้า clears the mid-month advance; หัก สะสม, ปกส. ...). These rows carry
                 "in_total": False: they are inside the gross already, shown so the owner sees where รวมจ่าย goes, not added to rows_total.

The driver summary is also the fallback source of route-truck revenue (scripts/import_driver_sales.py, payroll-PDF mode). That is NOT
booked here: its revenue needs Drivers.xlsx (truck per driver), the card fuel already in the books and the Route Summary's cash fuel, and
its replace scope is the whole month (dsales:YYYY-MM:* and driver_sales_monthly by month, where Drivers.xlsx is the main source). It
is not cleanly separable from this file alone, so it stays with its own importer.
"""
from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from datetime import date, datetime
from typing import Optional

from app import acc_payroll
from app.inbox_common import (THAI_MONTHS, Dropped, be_date, empty_preview, entry_amount, fix_thai_text, month_conflict,
                              month_label, thai_month_in, unbook_batches, vote, workbook)

KIND = "payroll"
LABEL = "เงินเดือน (HQ)"
FEEDS = ("hq_salary",)

GROUPS = ("FAC", "DRV", "KK")
PREFIX = {"SB-FR": "FAC", "SB-PD": "DRV", "SB-KK": "KK"}
PEOPLE_GROUP = {"FAC": "factory", "DRV": "drivers", "KK": "kk"}          # employee_pay_months.grp / payroll_people.READERS
GROUP_TH = {"FAC": "ช่าง + ออฟฟิศ + ฝ่ายผลิต สระบุรี", "DRV": "พขร. (จัดส่ง) สระบุรี", "KK": "แก่งคอย"}
ALIAS = {"SB-KK073": "SB-KK073S"}                                         # the same person, written two ways (import_payroll_people.py)
CODE = re.compile(r"SB-(?:FR|PD|KK)\d+[A-Z]?", re.I)

W_TITLE, W_PERIOD, W_PRINTED, W_NAME = 3.0, 2.0, 1.0, 0.5
W_TITLE_PAPER = 0.75        # the title of a slip / cash list / sign sheet: HQ copies these sheets and leaves the old heading (the
                            # drivers' slips of Aug (corrected) and Sep 2569 still say "ประจำเดือน : กรกฎาคม 2569"), so the pay date and
                            # the period in the body outweigh it; alone it still beats the file name
W_CODES, W_PEOPLE = 3.0, 3.0

# the deduction keys of the per-person readers, in acc_payroll.DEDUCTIONS order (their accounts)
DED_KEYS = ("savings", "advance", "loan", "common", "card", "shortage", "other", "sso")
DED_ROLE = {"1500": "advance"}                                            # clearing the mid-month advance; the rest: "info"

NOTES = {
    "slip": "สลิปเงินเดือนรายคน — คนชุดเดียวกับใบสรุปเงินเดือนของเดือนนี้ ยอดนับจากใบสรุปแล้ว เก็บไว้กับเดือนเท่านั้น ไม่ลงบัญชีซ้ำ",
    "cash": "รายชื่อรับเงินสด — เฉพาะคนที่รับเงินสด (คนชุดเดียวกับใบสรุปเงินเดือน) ยอดนับจากใบสรุปแล้ว เก็บไว้กับเดือนเท่านั้น ไม่ลงบัญชีซ้ำ",
    "sign": "ใบเซ็นรับสลิป / รับเงิน — คนชุดเดียวกับใบสรุปเงินเดือน ยอดนับจากใบสรุปแล้ว เก็บไว้กับเดือนเท่านั้น ไม่ลงบัญชีซ้ำ",
    "other": "เอกสารเงินเดือนที่ไม่ใช่ใบสรุป (ไม่พบกล่องสรุปรายหัก) — เก็บไว้กับเดือน ไม่ลงบัญชี",
}


# ------------------------------------------------------------------ what the document is
def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def doc_type(text: str) -> str:
    """'summary' / 'slip' / 'cash' / 'sign' / 'other' from the words INSIDE (titles may be stale: the box decides a summary)."""
    t = _flat(text)
    if "สลิปเงินเดือน" in t and "ประจำเดือน" in t:
        return "slip"
    if re.search(r"สรุปรายหัก|รายการหัก", t):
        return "summary"
    if re.search(r"รายชื่อรับเงินสด|รวมสดเฉพาะค่าแรง|รับเงินสด", t):
        return "cash"
    if re.search(r"เซ็นต?์?รับสลิป|เซ็นต?์?รับเงิน|โอนเข้าบัญชี", t):
        return "sign"
    if "จำนวนเงิน" in t and "รวมรับ" not in t:                     # one money column per person, no title left: the sign sheet has a
        return "sign" if "ลงชื่อ" in t else "cash"                  # ลงชื่อ column, the Kaeng Khoi cash list a หมายเหตุ one
    return "other"


def codes_in(text: str) -> list[str]:
    return [c.upper() for c in CODE.findall(text or "")]


def group_of_codes(codes: list[str]) -> Optional[str]:
    n = Counter(PREFIX.get(c[:5]) for c in codes if PREFIX.get(c[:5]))
    return n.most_common(1)[0][0] if n else None


def group_of_title(text: str) -> Optional[str]:
    t = _flat(text)
    if "แก่งคอย" in t:
        return "KK"
    if re.search(r"ช่าง\+ออฟฟิศ|ฝ่ายผลิต|ออฟฟิศ", t):
        return "FAC"
    if re.search(r"จัดส่ง|พขร", t):
        return "DRV"
    return None


def branch_of_title(text: str) -> Optional[str]:
    t = _flat(text)
    if "แก่งคอย" in t:
        return "แก่งคอย"
    if "สระบุรี" in t:
        return "สระบุรี"
    return None


def stale_title(text: str) -> Optional[str]:
    """A summary whose title is another paper's (HQ copied the sheet and kept the old heading)."""
    t = _flat(text)
    if re.search(r"เซ็นต?์?รับสลิป", t):
        return "ใบเซ็นรับสลิป"
    if "รับเงินสด" in t or "รายชื่อรับเงินสด" in t:
        return "รายชื่อรับเงินสด"
    return None


# ------------------------------------------------------------------ months
def _ym(mm: int, yy: int) -> Optional[str]:
    y = yy if yy > 1900 else 2500 + yy
    y = y - 543 if y > 2400 else y
    return f"{y}-{mm:02d}" if 1 <= mm <= 12 and 2020 <= y <= 2040 else None


def months_in(text: str) -> list[str]:
    """Every 'ตุลาคม 2569' / 'เดือนตุลาคม2569' in the text -> ['2026-10', ...]; a one-letter typo ('พฤภาคม') still counts."""
    out = []
    for m in re.finditer(r"([ก-๙]{3,})\s*(?:พ\.ศ\.\s*)?(25\d\d)(?!\d)", text or ""):
        word, year = m.group(1).replace("ำ", "า"), int(m.group(2))     # fonts that print า as ำ ("กรกฎำคม"); no month name has ำ
        hit = next((i for i, name in enumerate(THAI_MONTHS) if word.endswith(name)), None)
        if hit is None:
            for cut in (word, re.sub(r"^(ประจำ)?เดือน", "", word)):
                close = difflib.get_close_matches(cut, THAI_MONTHS, n=1, cutoff=0.8)
                if close:
                    hit = THAI_MONTHS.index(close[0])
                    break
        if hit is not None:
            ym = _ym(hit + 1, year)
            if ym:
                out.append(ym)
    return out


def title_month(text: str) -> Optional[str]:
    found = months_in(text)
    if found:
        return Counter(found).most_common(1)[0][0]
    return thai_month_in(text)


PERIOD = re.compile(r"(?:1[01]|26)\s*-\s*25\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})")
PERIOD_FULL = re.compile(r"26\s*/\s*\d{1,2}\s*/\s*\d{2,4}\s*-\s*25\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})")


def period_month(text: str) -> Optional[str]:
    """The half-month / month period printed in the body: '11-25/10/2569', '11-25/10/69', '26/09/2569 - 25/10/2569' -> '2026-10'."""
    found = [_ym(int(m.group(1)), int(m.group(2))) for rx in (PERIOD, PERIOD_FULL) for m in rx.finditer(text or "")]
    found = [x for x in found if x]
    return Counter(found).most_common(1)[0][0] if found else None


def printed_month(text: str) -> Optional[str]:
    """The print date at the top of a Saraburi summary ('01/11/2569'): the newest month whose 25th is on or before it."""
    for line in (text or "").splitlines()[:60]:
        line = line.strip()
        if re.fullmatch(r"\d{1,2}/\d{1,2}/25\d\d", line):
            d = be_date(line)
            if d:
                return f"{d.year}-{d.month:02d}" if d.day >= 25 else _prev(d)
    return None


def _prev(d: date) -> str:
    y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return f"{y}-{m:02d}"


def name_month(name: str) -> Optional[str]:
    m = title_month(name)
    if m:
        return m
    for i, full in enumerate(THAI_MONTHS):                                      # "มกราคม.69"
        mm = re.search(full + r"[\s.]*(6\d)(?!\d)", name or "")
        if mm:
            return _ym(i + 1, int(mm.group(1)))
    mm = re.search(r"(?<!\d)(\d{2})\s*[-.]\s*(6\d|25\d\d)(?!\d)", name or "")         # "02-69", "(แก้ไข01-69"
    return _ym(int(mm.group(1)), int(mm.group(2))) if mm else None


# ------------------------------------------------------------------ reading the file
def _pdf_parts(data: bytes) -> dict:
    """{'text': every page, 'header': page 1 above the first person (the title band), 'lines': every text line}."""
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages = [pg.get_text() for pg in doc]
        header = ""
        if doc.page_count:
            words = doc[0].get_text("words")
            tops = [w[1] for w in words if CODE.fullmatch(w[4].strip())]
            if tops:
                top = min(tops)
                above = sorted((w for w in words if w[3] <= top + 1), key=lambda w: (round(w[1] / 3), w[0]))
                lines: dict = {}
                for w in above:
                    lines.setdefault(round(w[1] / 3), []).append(w[4])
                header = "\n".join(" ".join(v) for _k, v in sorted(lines.items()))
            else:
                header = pages[0][:1500]
    text = fix_thai_text("\n".join(pages))
    return {"text": text, "header": fix_thai_text(header), "lines": text.splitlines()}


def _main_sheet(sheets: dict) -> Optional[tuple[str, list]]:
    """The per-person table of a payroll workbook: ตารางรายรับ-จ่ายพนง. (Saraburi) / สรุป (Kaeng Khoi), else the sheet with the most
    SB- codes that has the รวมรับ / คงเหลือ headers. Never the slip / savings / ตกหล่น sheets."""
    best, score = None, 0
    for name, rows in (sheets or {}).items():
        flat = _flat(" ".join(str(c) for r in rows[:12] for c in r if isinstance(c, str)))
        if not ("รวมรับ" in flat and "คงเหลือ" in flat):
            continue
        n = len({c for r in rows for c in r if isinstance(c, str) for c in codes_in(c)})
        n += 1000 if ("รายรับ" in name or name.strip() == "สรุป") else 0
        if n > score:
            best, score = (name, rows), n
    return best


def _cell_text(v) -> str:
    if isinstance(v, (datetime, date)):
        y = v.year - 543 if v.year > 2400 else v.year
        return f"{v.day:02d}/{v.month:02d}/{y + 543}"
    return "" if v is None else str(v)


def _xlsx_parts(rows: list) -> dict:
    """The same parts as _pdf_parts, from the main sheet's rows: header = rows above the first SB- code; the printed total row."""
    first = next((i for i, r in enumerate(rows) if any(isinstance(c, str) and CODE.fullmatch(c.strip()) for c in r[:8])), len(rows))
    header = "\n".join(" ".join(_cell_text(c).strip() for c in r if _cell_text(c).strip()) for r in rows[:first])
    total, end = None, len(rows)
    for at in range(first, len(rows)):
        r = rows[at]
        lead = " ".join(_cell_text(c) for c in r[:4]).strip()
        if lead.startswith("รวม"):
            nums = [float(c) for c in r if isinstance(c, (int, float)) and not isinstance(c, bool)]
            for i in range(len(nums) - 2):
                a, b, c = nums[i:i + 3]
                if a > 0 and b >= 0 and c > 0 and abs(a - b - c) < 1 and (total is None or a > total["gross"]):
                    total = {"gross": a, "deduct_total": b, "net": c}
            if total:
                end = at + 1
                break
    # the body stops at the total row: HQ leaves old rows and notes below it (the corrected Jan 2569 Kaeng Khoi workbook), which are
    # not this month's period
    lines = [_cell_text(c).strip() for r in rows[:end] for c in r if _cell_text(c).strip()]
    return {"text": "\n".join(lines), "header": header, "lines": lines, "total": total}


# ------------------------------------------------------------------ sniff
def sniff(f: Dropped) -> float:
    if f.is_scan:
        return 0.0
    if f.sheets is not None:
        main = _main_sheet(f.sheets)
        if not main:
            return 0.0
        codes = [c for r in main[1] for c in r if isinstance(c, str) for c in codes_in(c)]
        return 0.95 if len(codes) >= 3 else 0.0
    if f.ext != ".pdf":
        return 0.0
    codes = codes_in(f.text)
    if len(codes) < 3:
        return 0.0
    kind = doc_type(f.text)
    if kind == "summary":
        return 0.95
    if kind in ("slip", "cash", "sign"):
        return 0.9
    return 0.6 if re.search(r"รวมรับ|คงเหลือ|เงินเดือน", _flat(f.text)) else 0.0


# ------------------------------------------------------------------ the database (read only)
def _employees() -> Optional[dict]:
    """{CODE: {'id', 'branch'}} or None when the database is not reachable (tests, no .env)."""
    from app import supabase
    if not supabase.configured():
        return None
    try:
        rows = supabase.select_all("employees", {"select": "id,code,branch", "order": "code.asc"})
    except Exception:
        return None
    return {str(e["code"]).strip().upper(): e for e in rows if e.get("code")}


def _booked(grp: str) -> Optional[list[dict]]:
    """The payroll entries already in the books for this group: [{'month', 'wages', 'net'}] (None when the database is not reachable)."""
    from app import supabase
    if not supabase.configured():
        return None
    try:
        rows = supabase.select_all("acc_entries", {"select": "source_id,acc_lines(account,debit,credit)", "source": "eq.payroll",
                                                   "source_id": f"like.payroll:*:{grp}", "order": "source_id.asc"})
    except Exception:
        return None
    out = []
    for r in rows:
        parts = str(r.get("source_id") or "").split(":")
        if len(parts) != 3:
            continue
        lines = r.get("acc_lines") or []
        wages = sum(float(ln.get("debit") or 0) for ln in lines if ln.get("account") in ("5400", "6200"))
        net = sum(float(ln.get("credit") or 0) for ln in lines if ln.get("account") == "1100")
        out.append({"month": parts[1], "wages": round(wages, 2), "net": round(net, 2)})
    return out


def _emp(emp: Optional[dict], code: str) -> Optional[dict]:
    if not emp:
        return None
    return emp.get(ALIAS.get(code, code)) or emp.get(code)


# ------------------------------------------------------------------ conflicts other than the month
def _pick(signals: list[dict], what: str, choices: dict, show) -> tuple[Optional[str], list[dict]]:
    """vote() + a conflict when the signals disagree (the owner's choice wins), for 'group' and 'branch'."""
    if choices.get(what):
        return choices[what], []
    best, against = vote(signals, what)
    if not against:
        return best, []
    mine = [s for s in signals if s.get("what") == what and s.get("value")]
    values = list(dict.fromkeys([best] + [s["value"] for s in against]))
    src = lambda v: ", ".join(s["source"] for s in mine if s["value"] == v)
    head = {"group": "กลุ่มเงินเดือนไม่ตรงกัน", "branch": "สาขาไม่ตรงกัน"}[what]
    return None, [{"key": what, "message": f"{head}: " + " แต่ ".join(f"{src(v)} บอก {show(v)}" for v in values),
                   "options": [{"value": v, "label": f"{show(v)} ({src(v)})"} for v in values], "default": best}]


# ------------------------------------------------------------------ read
def read(f: Dropped) -> dict:
    p = empty_preview(KIND, LABEL)
    choices = f.choices or {}
    xlsx = f.sheets is not None or f.ext in (".xlsx", ".xlsm", ".xls")
    if xlsx:
        try:
            main = _main_sheet(workbook(f.data, f.name))
        except Exception:
            main = None
        if not main:
            p.update(store_only=True, note="ไม่พบตารางเงินเดือนรายคน (รวมรับ / คงเหลือ) ในไฟล์")
            return p
        parts = _xlsx_parts(main[1])
        kind = "summary"
    else:
        parts = _pdf_parts(f.data)
        parts["total"] = None
        kind = doc_type(parts["text"])
    p["doc"] = kind

    # ---- signals: group, branch (title), month
    codes = codes_in(parts["text"])
    sig = []
    g_title, g_codes, g_name = group_of_title(parts["header"]), group_of_codes(codes), group_of_title(f.name)
    if g_codes:
        sig.append({"what": "group", "source": f"รหัสพนักงานในไฟล์ ({len(codes)} รหัส)", "value": g_codes, "weight": W_CODES})
    if g_title:
        sig.append({"what": "group", "source": "หัวกระดาษ", "value": g_title, "weight": W_TITLE})
    if g_name:
        sig.append({"what": "group", "source": "ชื่อไฟล์", "value": g_name, "weight": W_NAME})
    m_title, m_period, m_name = title_month(parts["header"]), period_month(parts["text"]), name_month(f.name)
    m_printed = printed_month(parts["header"] if xlsx else parts["text"]) if kind in ("summary", "slip") else None
    w_title = W_TITLE if kind == "summary" else W_TITLE_PAPER
    for src, val, w in (("หัวกระดาษ", m_title, w_title), ("งวด 11-25 ในเนื้อหา", m_period, W_PERIOD),
                        ("วันที่จ่าย" if kind == "slip" else "วันที่พิมพ์", m_printed, W_PRINTED), ("ชื่อไฟล์", m_name, W_NAME)):
        if val:
            sig.append({"what": "month", "source": src, "value": val, "weight": w})
    month, mconf = month_conflict(sig, choices)
    grp, gconf = _pick(sig, "group", choices, lambda v: GROUP_TH.get(v, v))
    p["signals"], p["conflicts"] = sig, mconf + gconf
    p["month"] = month
    use_month = month or vote(sig, "month")[0]
    use_grp = grp or vote(sig, "group")[0]
    p["group"] = use_grp

    if kind != "summary":
        # kept, never booked: it files under the month most signals give (the engine shows no question for a stored file), and a
        # disagreement is said in a flag so the owner sees the heading was stale
        if mconf and use_month:
            p["month"] = use_month
            p["flags"].append(f"{mconf[0]['message']} — เก็บไว้กับเดือน {month_label(use_month)} (หัวกระดาษของเอกสารนี้มักไม่ได้แก้)")
        p.update(store_only=True, note=NOTES[kind] + (f" ({GROUP_TH[use_grp]} {month_label(use_month)})" if use_grp else ""))
        return p
    if not use_grp or not use_month:
        p["flags"].append("หากลุ่มหรือเดือนของใบสรุปไม่ได้ — ตรวจไฟล์")
        p.update(store_only=True, note="ใบสรุปเงินเดือนที่ระบุกลุ่ม/เดือนไม่ได้ ยังไม่ลงบัญชี")
        return p
    old = None if xlsx else stale_title(parts["header"])         # the workbook keeps every printout's heading in its top rows
    if old:
        p["flags"].append(f"หัวกระดาษเขียนว่า \"{old}\" (หัวเก่าที่ HQ ไม่ได้แก้) แต่เนื้อหาเป็นใบสรุปเงินเดือน (มีกล่องสรุปรายหัก) — อ่านเป็นใบสรุป")

    # ---- the people (per-person readers) and the printed totals
    from app import payroll_people
    try:
        ppl = payroll_people.parse_bytes(PEOPLE_GROUP[use_grp], f.data, f.name, use_month)
    except Exception as e:                                           # the totals can still be booked; the pay history waits
        ppl = None
        p["flags"].append(f"อ่านรายคนไม่ได้ ({e}) — ลงบัญชีจากยอดรวมได้ แต่ประวัติเงินเดือนรายคนจะไม่ถูกแทนที่")
    s = _summary(parts, ppl, p["flags"], xlsx)
    if s is None:
        p["flags"].append("ไม่พบยอดรวมรับ / รวมจ่าย / คงเหลือ ในใบสรุป")
        p.update(store_only=True, note="อ่านยอดรวมของใบสรุปไม่ได้ ยังไม่ลงบัญชี")
        return p

    # ---- branch from the people (employees table) vs the title
    emp = _employees()
    people = (ppl or {}).get("people") or []
    matched = []
    if emp is not None:
        matched = [e for e in (_emp(emp, r["code"]) for r in people) if e]
        by_branch = Counter(e.get("branch") for e in matched if e.get("branch"))
        if by_branch:
            b, n = by_branch.most_common(1)[0]
            sig.append({"what": "branch", "source": f"พนักงานในไฟล์ ({n} จาก {len(people)} คน)", "value": b, "weight": W_PEOPLE})
    else:
        p["flags"].append("ยังไม่ได้เทียบรหัสกับรายชื่อพนักงาน (ต่อฐานข้อมูลไม่ได้)")
    b_title = branch_of_title(parts["header"])
    if b_title:
        sig.append({"what": "branch", "source": "หัวกระดาษ", "value": b_title, "weight": W_TITLE})
    branch, bconf = _pick(sig, "branch", choices, lambda v: v)
    p["conflicts"] += bconf
    use_branch = branch or vote(sig, "branch")[0] or acc_payroll.GROUPS[use_grp][0]
    if people:
        miss = sorted({r["code"] for r in people if not _emp(emp, r["code"])}) if emp is not None else []
        p["people"] = {"count": len(people), "matched": len(matched) if emp is not None else None, "unmatched": miss}
        if miss:
            p["flags"].append(f"ไม่พบรหัสในรายชื่อพนักงาน {len(miss)} คน: {', '.join(miss[:12])}" + (" ..." if len(miss) > 12 else ""))

    # ---- the batch
    batch = build_batch(use_month, use_grp, use_branch, s, ppl, emp, f.name)
    p["batches"] = [batch]
    p["flags"] += batch.pop("_flags")

    # ---- the same totals already booked? (the same file again under a stale name)
    booked = _booked(use_grp)
    wages, net = s["gross"] - sum(s["reimburse"]), s["net"] - sum(s["reimburse"])
    for b in booked or []:
        if abs(b["wages"] - wages) <= 1 and abs(b["net"] - net) <= 1:
            if b["month"] == use_month:
                p["flags"].append(f"{GROUP_TH[use_grp]} {month_label(use_month)} ลงบัญชีไว้แล้วด้วยยอดเดียวกันทุกบาท — ลงอีกครั้งจะแทนที่ของเดิม ไม่เพิ่ม")
            elif choices.get("duplicate") != "book":
                p["conflicts"].append({
                    "key": "duplicate",
                    "message": f"ยอดเงินเดือน {GROUP_TH[use_grp]} ในไฟล์นี้ (ค่าจ้าง {wages:,.0f} สุทธิ {net:,.0f}) เท่ากับเดือน "
                               f"{month_label(b['month'])} ที่ลงบัญชีไว้แล้วทุกบาท — น่าจะเป็นไฟล์เดือน {month_label(b['month'])} ที่ชื่อ/หัวกระดาษไม่ได้แก้",
                    "options": [{"value": "store", "label": "เก็บไว้เฉยๆ ไม่ลงบัญชี"},
                                {"value": "book", "label": f"ลงบัญชีเป็นเดือน {month_label(use_month)} ต่อไป"}],
                    "default": "store"})
            break
    if choices.get("duplicate") == "store":
        p.update(store_only=True, batches=[], note="เจ้าของเลือกเก็บไว้เฉยๆ: ยอดซ้ำกับเดือนที่ลงบัญชีไว้แล้ว")
        return p
    p["note"] = (f"ใบสรุปเงินเดือน {GROUP_TH[use_grp]} {month_label(use_month)} · {len(people)} คน · รวมรับ {s['gross']:,.0f}"
                 + (f" (รวมคืนเงินสดย่อย {sum(s['reimburse']):,.0f} ซึ่งไม่ใช่เงินเดือน)" if s["reimburse"] else "")
                 + f" · รวมจ่าย {s['deductions']:,.0f} · คงเหลือ {s['net']:,.0f}")
    return p


def _baht(x: float) -> float:
    """Half up to the baht, as Excel prints the summary ('870,472.5' -> 870,473; Python's round() would give 870,472)."""
    return float(math.floor(float(x) + 0.5))


def _summary(parts: dict, ppl: Optional[dict], flags: list, xlsx: bool = False) -> Optional[dict]:
    """{gross, deductions, net, parts (8, acc_payroll order), reimburse} from the printed totals, checked against the people.
    A workbook carries satang the printed summary rounds away: it is booked in whole baht, exactly as its PDF would be, so dropping the
    xlsx and the PDF of the same month books the same numbers."""
    people = (ppl or {}).get("people") or []
    extras = (ppl or {}).get("extras") or []
    rnd = _baht if xlsx else (lambda x: round(float(x), 2))
    pd = [rnd(sum(float((r.get("deductions") or {}).get(k) or 0) for r in people)) for k in DED_KEYS]
    s = None
    if parts.get("total") is None:                                   # PDF: the total row is found by its arithmetic
        try:
            s = acc_payroll.parse_summary(parts["lines"])
        except ValueError:
            s = None
    else:                                                            # xlsx: the รวม row of the main sheet
        t = parts["total"]
        s = {"gross": rnd(t["gross"]), "deductions": rnd(t["deduct_total"]), "net": rnd(t["net"]), "parts": None, "reimburse": []}
    if s is None and people:                                         # no printed total readable: the people + extras, flagged
        s = {"gross": rnd(sum(r["gross"] for r in people) + sum(x["gross"] for x in extras)),
             "deductions": rnd(sum(r["deduct_total"] for r in people) + sum(x["deduct_total"] for x in extras)),
             "net": rnd(sum(r["net"] for r in people) + sum(x["net"] for x in extras)), "parts": None, "reimburse": []}
        flags.append("ไม่พบแถวยอดรวมที่พิมพ์ — ใช้ผลรวมรายคนแทน")
    if s is None:
        return None
    if ppl is not None:
        back = [rnd(x["gross"]) for x in extras if x.get("gross")]
        if parts.get("total") is not None or not s["reimburse"]:
            s["reimburse"] = back                                    # the xlsx has no half-month labels to read: the extras are the rows
        elif abs(sum(back) - sum(s["reimburse"])) > 1:
            flags.append(f"แถวคืนเงินสดย่อยในตาราง {sum(back):,.0f} ไม่เท่ากับที่อ่านจากข้อความ {sum(s['reimburse']):,.0f} — ตรวจ")
        if s["parts"] is None and people and abs(sum(pd) - s["deductions"]) <= 1:
            s["parts"] = pd                                          # no box (xlsx) or unreadable: the people's own columns, which add up
            if not xlsx:
                flags.append("แยกรายการหักจากคอลัมน์รายคน (กล่องสรุปรายหักอ่านไม่ได้) — ผลรวมเท่ากับรวมจ่าย")
    return s


def build_batch(month: str, grp: str, branch: str, s: dict, ppl: Optional[dict], emp: Optional[dict], file_name: str) -> dict:
    """The one batch of a summary: the ledger entry, the rows with their roles, the checks, and the employee_pay_months rows."""
    _branch, wage_acc, _label = acc_payroll.GROUPS[grp]
    day = f"{month}-25"
    flags: list = []
    entry = acc_payroll.build_entry(month, grp, s, file_name)
    diff = round(sum(ln["debit"] for ln in entry["lines"]) - sum(ln["credit"] for ln in entry["lines"]), 2)
    if 0 < abs(diff) <= 1.5:                                         # printed totals each rounded to the baht: the net paid takes the satang
        net_line = next(ln for ln in entry["lines"] if ln["account"] == "1100")
        net_line["credit"] = round(net_line["credit"] + diff, 2)
        flags.append(f"ปัดเศษ {diff:+,.2f} บาท ที่ยอดจ่ายสุทธิ (ยอดรวมที่พิมพ์ปัดทีละช่อง)")
    if branch and branch != entry["branch"]:
        entry["branch"] = branch
        for ln in entry["lines"]:
            ln["branch"] = branch
    people = (ppl or {}).get("people") or []
    extras = (ppl or {}).get("extras") or []
    rows = []
    for r in people:
        flag = None
        if emp is not None and not _emp(emp, r["code"]):
            flag = "ไม่พบรหัสในรายชื่อพนักงาน"
        if r.get("refund"):
            flag = "; ".join(x for x in (flag, f"รวมเงินคืนสะสม/จ่ายค้าง {r['refund']:,.0f} (ตามหมายเหตุ HQ)") if x)
        rows.append({"date": day, "text": f"{r['code']} {r['name']}" + (f" · {r['dept']}" if r.get("dept") else "") + f" · {r['days']:g} วัน",
                     "party": r["name"], "amount": round(r["gross"], 2), "role": "wages", "account": wage_acc, "flag": flag, "code": r["code"]})
    back = s.get("reimburse") or []
    labels = [x.get("label") or "" for x in extras if x.get("gross")]
    for i, amt in enumerate(back):
        rows.append({"date": day, "text": (labels[i] if i < len(labels) and labels[i] else "คืนเงินสดย่อย")
                     + " — คืนเงินสดย่อย (นับจากเอกสารสิ้นเดือนแล้ว ไม่ใช่เงินเดือน)",
                     "party": "เงินสดย่อย", "amount": round(amt, 2), "role": "funding", "account": "1100", "flag": None})
    if s.get("parts"):
        for (name, acc), amt in zip(acc_payroll.DEDUCTIONS, s["parts"]):
            if amt:
                rows.append({"date": day, "text": f"หัก {name}", "party": None, "amount": round(amt, 2), "role": DED_ROLE.get(acc, "info"),
                             "account": acc, "flag": None, "in_total": False})
    else:
        rows.append({"date": day, "text": "หัก (แยกรายการไม่ได้ — พักไว้ 1500)", "party": None, "amount": round(s["deductions"], 2),
                     "role": "advance", "account": "1500", "flag": "แยกรายการหักไม่ได้ — ตรวจ", "in_total": False})
        flags.append("แยกรายการหักไม่ได้: ยอดหักทั้งหมดพักไว้ที่ 1500 — ตรวจ")
    rows_total = round(sum(r["amount"] for r in rows if r.get("in_total", True)), 2)

    # ---- checks: nothing counted twice, nothing lost
    checks = []
    tol = 1.0 + 0.5 * len(people)                                    # the PDF prints each person rounded to the baht
    if people:
        pg = round(sum(r["gross"] for r in people) + sum(back), 2)
        checks.append({"what": "รายคน + คืนเงินสดย่อย = รวมรับที่พิมพ์", "ok": abs(pg - s["gross"]) <= tol,
                       "detail": f"{pg:,.2f} / {s['gross']:,.2f}"})
        pdd = round(sum(r["deduct_total"] for r in people) + sum(x.get("deduct_total") or 0 for x in extras), 2)
        checks.append({"what": "รายการหักรายคน = รวมจ่ายที่พิมพ์", "ok": abs(pdd - s["deductions"]) <= tol, "detail": f"{pdd:,.2f} / {s['deductions']:,.2f}"})
        pn = round(sum(r["net"] for r in people) + sum(x.get("net") or 0 for x in extras), 2)
        checks.append({"what": "คงเหลือรายคน + คืนเงินสดย่อย = คงเหลือที่พิมพ์", "ok": abs(pn - s["net"]) <= tol, "detail": f"{pn:,.2f} / {s['net']:,.2f}"})
        codes = [r["code"] for r in people]
        dup = sorted({c for c in codes if codes.count(c) > 1})
        checks.append({"what": "ไม่มีรหัสพนักงานซ้ำ", "ok": not dup, "detail": ", ".join(dup)})
        if s.get("parts"):
            by_acc: dict = {}                                        # 4900 holds two columns whose order differs between sheets
            for (_n, acc), k, amt in zip(acc_payroll.DEDUCTIONS, DED_KEYS, s["parts"]):
                box, cols = by_acc.setdefault(acc, [0.0, 0.0])
                by_acc[acc] = [box + amt, cols + sum(float((r.get("deductions") or {}).get(k) or 0) for r in people)]
            off = {a: v for a, v in by_acc.items() if abs(v[0] - v[1]) > tol}
            checks.append({"what": "กล่องสรุปรายหัก = คอลัมน์หักรายคน (ทีละบัญชี)", "ok": not off,
                           "detail": ", ".join(f"{a}: {v[0]:,.0f} / {v[1]:,.0f}" for a, v in off.items())})
    checks.append({"what": "รวมรับ - รวมจ่าย = คงเหลือ", "ok": abs(s["gross"] - s["deductions"] - s["net"]) <= 1,
                   "detail": f"{s['gross']:,.2f} - {s['deductions']:,.2f} = {s['net']:,.2f}"})
    if s.get("parts"):
        checks.append({"what": "รายการหัก 8 ช่องรวมเท่ารวมจ่าย", "ok": abs(sum(s["parts"]) - s["deductions"]) <= 1,
                       "detail": f"{sum(s['parts']):,.2f} / {s['deductions']:,.2f}"})
    dr = round(sum(ln["debit"] for ln in entry["lines"]), 2)
    cr = round(sum(ln["credit"] for ln in entry["lines"]), 2)
    checks.append({"what": "รายการบัญชีสมดุล (เดบิต = เครดิต)", "ok": abs(dr - cr) <= 0.01, "detail": f"{dr:,.2f} / {cr:,.2f}"})
    wage_dr = round(sum(ln["debit"] for ln in entry["lines"] if ln["account"] == wage_acc), 2)
    checks.append({"what": "คืนเงินสดย่อยไม่ถูกนับเป็นเงินเดือน", "ok": abs(wage_dr - (s["gross"] - sum(back))) <= 0.01,
                   "detail": f"ค่าจ้าง {wage_dr:,.2f} = รวมรับ {s['gross']:,.2f} - คืนเงินสดย่อย {sum(back):,.2f}"})
    for c in checks:
        if not c["ok"]:
            flags.append(f"ตรวจไม่ผ่าน: {c['what']} ({c['detail']})")

    pay = None
    if ppl is not None and people:
        pay = {"month": month, "grp": PEOPLE_GROUP[grp], "rows": [
            {"month": month, "grp": PEOPLE_GROUP[grp], "code": r["code"],
             "employee_id": (_emp(emp, r["code"]) or {}).get("id"),
             "name": r["name"], "dept": r.get("dept"), "days": r["days"], "parts": r["parts"], "refund": r.get("refund") or 0,
             "gross": r["gross"], "deductions": r["deductions"], "deduct_total": r["deduct_total"], "net": r["net"],
             "source": r.get("source"), "file": file_name, "flags": r.get("flags") or [], "details": r.get("details") or {}}
            for r in people]}
    return {"key": f"payroll:{month}:{grp}", "feed": "hq_salary", "source": "payroll", "branch": entry["branch"], "month": month,
            "title": f"เงินเดือน {GROUP_TH[grp]} {month_label(month)}", "rows": rows, "file_total": round(s["gross"], 2),
            "rows_total": rows_total, "entries": [entry], "checks": checks, "pay_months": pay, "_flags": flags}


# ------------------------------------------------------------------ book / unbook
def _unbook_pay(preview: dict) -> None:
    from app import supabase
    for b in preview.get("batches") or []:
        pm = b.get("pay_months")
        if pm and pm.get("month") and pm.get("grp"):
            supabase.delete("employee_pay_months", {"month": f"eq.{pm['month']}", "grp": f"eq.{pm['grp']}"})


def book(preview: dict, file_id: str) -> dict:
    """Replace, never add: the ledger entry of the key and employee_pay_months of exactly (month, group) go first, then the new ones."""
    from app import supabase
    from app.accounting import _insert_entries, _require
    _require()
    unbook(preview)
    total = 0.0
    for b in preview.get("batches") or []:
        _insert_entries(b.get("entries") or [])
        total += sum(entry_amount(e) for e in b.get("entries") or [])
        rows = (b.get("pay_months") or {}).get("rows") or []
        for i in range(0, len(rows), 200):
            supabase.insert("employee_pay_months", rows[i:i + 200])
    return {"batches": [b["key"] for b in preview.get("batches") or []], "booked": round(total, 2), "rebook": []}


def unbook(preview: dict) -> None:
    unbook_batches(preview)
    _unbook_pay(preview)
