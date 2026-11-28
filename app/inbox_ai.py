"""
บัญชี › กล่องรับไฟล์ › อ่านด้วย AI (phase 2; owner, 24 Nov 2026): a scan or a file no exact reader claims is read by Claude, page by page,
ONLY when the owner clicks 'อ่านด้วย AI (N หน้า)' (the page count is shown first, app/inbox_r_scan.py). The engine (app/inbox.py) calls
run() in a background thread and stores the preview it returns; the tokens spent are in preview["ai_usage"].

What comes back depends on what the pages turn out to be -- never on the file name (HQ forgets to rename files; a name is a hint):

* a branch's mid-month / end-of-month expense papers (the cover, handwritten รายการเบิก, fuel lists, mid-month advance lists, HR papers,
  receipts attached as proof): the same BATCH structure data/acc_docs/*.py holds, run through app/acc_docs.build -- so every page's lines
  are added up against the total written on it, and the cover's parts against the pages, exactly like the transcribed months. Booking
  goes through acc_docs.import_batch, so the review screen (/api/accounts/docs/*: the scan beside the lines read from it) works for
  these pages too; the page images are stored from bytes under the file (accdocs/inbox/<file id>/p<n>.jpg).
  One file = one half's papers: batch key "docs:<month>:<branch>:<mid|end>". Re-dropping that half replaces it, never adds to it.
* a single bill / invoice / statement (irrigation water, waterworks, an odd receipt): ONE suggested entry per bill (cost to accounts
  payable 2100, so the cheque that pays it later is "pays an existing bill", never a second cost: app/acc_cheques matches the
  next month's HQ cheque to it by amount and payee, or by the bill number in the cheque's note), for the owner to confirm. A PEA
  electricity bill is never booked from an AI read (stored only, the note says to drop PEA's own PDF: the PEA reader books it).
* a memo, a payslip, anything not for the books: store_only, with what it is.

Month, branch and half are decided by votes of independent signals (cover date range weight 3, the majority of line dates 2, branch text
on the papers 3, employee codes SB-FR / SB-PD = สระบุรี and SB-KK = แก่งคอย 2, truck plates matched to vehicles.branch 2, the file name
0.5). Whenever they disagree the preview carries a conflict and the engine books nothing until the owner picks (owner, 24 Nov 2026).

The raw page reads are kept in the preview (ai_pages), so answering a conflict rebuilds the preview from them (apply_choices) without
paying for a second read.

Large files: never one 20 MB PDF in one request -- each page goes as its own JPEG (about 2000 px on the long side), at most PARALLEL at a
time. Same model, client, structured output and fallback as the bill readers (app/extraction.py, app/cash_fuel.py).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Callable, Optional

from app import inbox_common as ic
from app.config import settings

logger = logging.getLogger("vehicle_tracker.inbox_ai")

KIND = "scan"                     # the previews made here are booked / unbooked by app/inbox_r_scan.py
PARALLEL = 4                      # pages read at the same time (owner's account limits; a 29-page file takes ~8 rounds)
MAX_PAGES = 60                    # the biggest monthly file seen is 32 pages; more than this is not one month's papers
LONG_SIDE = 2000                  # px; what the cash-fuel reader sends for the same handwriting
MAX_PARSE_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 16000         # a 30-line handwritten page with thinking fits well inside this (non-streaming limit with margin)
EFFORT = "high"                   # handwriting: the same effort the cash-fuel and bill readers use


class InboxAIError(RuntimeError):
    """str() is a Thai message safe to show the owner. A read that failed after pages were already sent carries what was spent:
    `usage` (the same shape as preview["ai_usage"]) and `partial_pages` (the page reads that did come back) -- the engine saves both,
    so a failed read never loses the tokens it cost (review 26 Nov 2026)."""
    usage: Optional[dict] = None
    partial_pages: Optional[list] = None


# ------------------------------------------------------------------ the chart the pages are booked to
# The accounts the transcribed months (data/acc_docs/2026_*.py, Jan-Oct 2026: 4,000+ lines) actually used, with what the lines said --
# the model picks one per line from this list only. PARTY is the party those months gave each account (the P&L drills down by party,
# so the AI's lines land in the same rows as the transcribed ones).
CHART = [
    ("1500", "เงินเบิกล่วงหน้าพนักงาน (ไม่ใช่ค่าใช้จ่าย)", "เบิกกลางเดือน / เบิกล่วงหน้า ของพนักงาน"),
    ("1510", "เงินกู้พนักงาน (ไม่ใช่ค่าใช้จ่าย)", "พนักงานยืมเงิน / กู้เงินโรงงาน"),
    ("2400", "คืนเงินสะสมพนักงาน (ไม่ใช่ค่าใช้จ่าย)", "จ่ายคืนเงินสะสมให้คนลาออก"),
    ("5050", "ซื้อน้ำแข็ง", "ค่าน้ำแข็งที่ซื้อมาขาย"),
    ("5100", "ค่าไฟฟ้า", "ค่าไฟโรงงาน (การไฟฟ้า)"),
    ("5200", "ค่าน้ำ - ชลประทาน", "ค่าน้ำชลประทาน"),
    ("5210", "ค่าน้ำ - ประปา", "ค่าน้ำประปา"),
    ("5300", "เกลือ", "ค่าเกลือ / เกลือล้างกรอง"),
    ("5310", "แอมโมเนีย", "แอมโมเนีย"),
    ("5320", "เคมีบำบัดน้ำ", "PAC คลอรีน โพลิเมอร์ ไส้กรอง RO กรวด / คาร์บอน test strip"),
    ("5330", "ถุงและบรรจุภัณฑ์", "ถุงน้ำแข็ง กระสอบ"),
    ("5400", "ค่าแรงฝ่ายผลิต", "ค่าแรงรายวัน ค่าแรงตกหล่น ช่างรายวัน"),
    ("5410", "ค่าล่วงเวลา (โอที)", "OT / โอที พนักงาน ชั่วโมง"),
    ("5500", "ซ่อมบำรุงเครื่องจักร", "ซ่อมเครื่องจักร ซ่อมบ่อ ปั๊ม มอเตอร์ ค่าบริการช่าง"),
    ("5510", "อะไหล่และวัสดุโรงงาน", "ของใช้โรงงาน ถุงมือ น้ำยาล้างจาน กระดาษชำระ ล้อ สี เครื่องมือช่าง"),
    ("6100", "ซ่อมรถ", "ซ่อมรถ อะไหล่รถ รถยก / ลากรถ"),
    ("6110", "ยางรถ", "ปะยาง ยางรถ"),
    ("6130", "น้ำมันเงินสด", "เติมน้ำมันรถ / รถยก / มอเตอร์ไซค์ (ค่าน้ำมันรถ)"),
    ("6200", "ค่าแรงพนักงานขับรถ", "เบี้ยเลี้ยงคนขับ ค่าแรงเด็กรถ เบี้ยขยันคนขับ"),
    ("6300", "ภาษีรถ / พรบ. / ตรวจสภาพ", "ค่าตรวจสภาพรถ ต่อภาษี พรบ."),
    ("7100", "เงินเดือนสำนักงาน", "ค่าแรงเสมียน เงินเดือนออฟฟิศ"),
    ("7110", "ประกันสังคม", "ประกันสังคม กองทุนเงินทดแทน"),
    ("7120", "ค่าดำเนินการแรงงานต่างด้าว", "บัตรชมพู วีซ่า ใบอนุญาตทำงาน"),
    ("7200", "โทรศัพท์และอินเทอร์เน็ต", "ค่าโทรศัพท์ WiFi"),
    ("7300", "วัสดุสำนักงาน", "เครื่องเขียน กระดาษ หมึก ค่าส่งเอกสาร ไปรษณีย์"),
    ("7400", "ค่าห้องพักพนักงาน", "ค่าห้องเช่า บ้านเช่า ค่าไฟบ้านพัก"),
    ("7500", "ค่าธรรมเนียม / ใบอนุญาต", "ค่าโอนเงิน ค่าธรรมเนียมธนาคาร ภาษีป้าย ค่าขยะ อบต."),
    ("7600", "ค่าทำบัญชีและภาษี", "ค่าทำบัญชี"),
    ("7900", "อื่นๆ", "ข้าว น้ำ ขนม พนักงาน ของไหว้พระ ค่ารักษาพยาบาล อื่นๆ"),
    ("7910", "ส่วย (ตำรวจ / ตม. / ตลาด)", "ตำรวจ จราจร ตม. ตลาด"),
]
ACCOUNTS = [c for c, _, _ in CHART]
PARTY = {"1500": "เบิกกลางเดือน", "1510": "เงินกู้พนักงาน", "2400": "คืนเงินสะสมพนักงาน", "5050": "ซื้อน้ำแข็ง", "5100": "ค่าไฟฟ้า",
         "5200": "ชลประทาน", "5210": "ประปา", "5300": "เกลือ", "5310": "แอมโมเนีย", "5320": "เคมีบำบัดน้ำ", "5330": "ถุงและบรรจุภัณฑ์",
         "5400": "ค่าแรงรายวัน", "5410": "โอทีพนักงาน", "5500": "ซ่อมเครื่องจักร", "5510": "ของใช้โรงงาน", "6100": "ซ่อมรถ", "6110": "ปะยาง",
         "6130": "น้ำมันเงินสด", "6200": "ค่าแรงคนขับ / เด็กรถ", "6300": "ตรวจสภาพรถ", "7100": "เงินเดือน", "7110": "ประกันสังคม",
         "7120": "แรงงานต่างด้าว", "7200": "โทรศัพท์ / อินเทอร์เน็ต", "7300": "เครื่องเขียน / เอกสาร", "7400": "ค่าห้อง / บ้านเช่า",
         "7500": "ธนาคาร (ค่าธรรมเนียม)", "7600": "ค่าทำบัญชี", "7900": "อื่นๆ", "7910": "ส่วย (ตำรวจ / ตม. / ตลาด)"}
WAGES = {"5400", "5410", "6200", "7100"}
PAGE_KINDS = ["cover", "expense", "fuel", "fuel_table", "advance", "memo", "payslip", "bill", "other"]
KIND_TH = {"cover": "ใบปก / สรุปค่าใช้จ่าย", "expense": "รายการเบิก", "fuel": "เบิกค่าน้ำมัน", "fuel_table": "ตารางค่าน้ำมัน (พิมพ์)",
           "advance": "เบิกกลางเดือน", "memo": "บันทึก / เอกสาร HR", "payslip": "สลิปเงินเดือน", "bill": "บิล / ใบแจ้งหนี้",
           "other": "อื่นๆ", "error": "อ่านไม่สำเร็จ"}
HALF_TH = {"mid": "กลางเดือน (26-10)", "end": "ปลายเดือน (11-25)"}
BILL_FEEDS = [(re.compile(r"ชลประทาน"), "water_irrigation", "5200"), (re.compile(r"ประปา"), "water_city", "5210"),
              (re.compile(r"การไฟฟ้า|PEA", re.I), "electricity", "5100")]

# ------------------------------------------------------------------ what one page read looks like
_num = {"anyOf": [{"type": "number"}, {"type": "null"}]}      # structured outputs take nullable as anyOf (app/purchase_extraction.py)
_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
# The API compiles at most MAX_UNIONS nullable / anyOf fields per schema (24 Nov 2026: 19 were refused with a 400 before any page was
# read). So only what is truly "maybe missing" is nullable; the bill and the cover parts are always there, with "" / 0 when not used.
MAX_UNIONS = 16
_LINE = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "date": {**_str, "description": "YYYY-MM-DD, Gregorian (2569 BE = 2026). A line without its own date has the date of the line above."},
        "text": {"type": "string", "description": "รายการ as written (Thai). Advance lists: the person's name and code. Fuel table: the truck."},
        "amount": {**_num, "description": "baht, a plain number"},
        "plate": {**_str, "description": "vehicle plate as written on a fuel / tyre / repair line, e.g. '1ฒถ 2085', '84-4607'"},
        "truck_number": {**_str, "description": "the fleet truck number, e.g. '(05)' -> '05'"},
        "account": {"type": "string", "enum": ACCOUNTS},
        "unsure": {"type": "boolean", "description": "true if the amount, date or truck could not be read with confidence"},
    },
    "required": ["date", "text", "amount", "plate", "truck_number", "account", "unsure"],
}
# a row of a branch's work record (บันทึกการทำงาน+เบิกกลางเดือน, แก่งคอย's end-of-month copy: owner, 28 Nov 2026 -- the source of
# แก่งคอย's days, OT and ค่าเที่ยว on the pay page). Numbers come as written (text): a blank cell is "", never a guess.
_WORK = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "code": {"type": "string", "description": "the staff code as printed, e.g. SB-KK001"},
        "name": {"type": "string", "description": "the name as printed"},
        "days": {"type": "string", "description": "วัน (days worked) as written, e.g. '31'; '' when blank"},
        "ot": {"type": "string", "description": "OT hours as written when a number, e.g. '15'; '' when blank or a word"},
        "trip": {"type": "string", "description": "ค่าเที่ยว (trip money, baht) as written, e.g. '10900'; '' when blank"},
        "note": {"type": "string", "description": "any word written in these cells instead of a number, e.g. 'เบี้ยขยัน'; else ''"},
        "unsure": {"type": "boolean", "description": "true if days, OT or ค่าเที่ยว could not be read with confidence"},
    },
    "required": ["code", "name", "days", "ot", "trip", "note", "unsure"],
}
PAGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "page_kind": {"type": "string", "enum": PAGE_KINDS},
        "title": {"type": "string", "description": "a short Thai title, as written on the page"},
        "date_from": {**_str, "description": "first day the page covers, YYYY-MM-DD"},
        "date_to": {**_str, "description": "last day the page covers, YYYY-MM-DD"},
        "written_total": {**_num, "description": "the total WRITTEN on the page (often red ink at the bottom); a cover: its grand total"},
        "cover_parts": {"type": "object", "additionalProperties": False,
                        "description": "a cover's parts in baht; 0 for a part that is not on the cover, all 0 on other pages",
                        "properties": {"expense": {"type": "number"}, "fuel": {"type": "number"}, "advance": {"type": "number"}},
                        "required": ["expense", "fuel", "advance"]},
        "lines": {"type": "array", "items": _LINE},
        "branch_hints": {"type": "array", "items": {"type": "string"}},
        "employee_codes": {"type": "array", "items": {"type": "string"}},
        "bill": {"type": "object", "additionalProperties": False,
                 "description": "for page_kind bill; on other pages every text empty, total null, account 7900",
                 "properties": {"party": {"type": "string"}, "doc_no": {"type": "string"}, "date": {"type": "string"},
                                "period_month": {"type": "string"}, "total": _num, "what": {"type": "string"},
                                "account": {"type": "string", "enum": ACCOUNTS}},
                 "required": ["party", "doc_no", "date", "period_month", "total", "what", "account"]},
        "work_rows": {"type": "array", "items": _WORK,
                      "description": "ONLY for a per-person work record table (บันทึกการทำงาน) with วันทำงาน / OT / ค่าเที่ยว columns; else []"},
        "summary": {"type": "string", "description": "one short Thai line: what this page is"},
        "person": {"type": "string", "description": "who a memo / payslip is about; empty if none"},
    },
    "required": ["page_kind", "title", "date_from", "date_to", "written_total", "cover_parts", "lines", "branch_hints",
                 "employee_codes", "bill", "work_rows", "summary", "person"],
}

PROMPT = """You read ONE scanned page of a Thai ice factory's papers (company สระบุรีวารีเทพ; two branches: สระบุรี, code SRB / SB, and
แก่งคอย, code KK). Most files are a branch's half-month expense papers sent to head office: the mid-month papers (กลางเดือน, the 26th to
the 10th) or the end-of-month papers (ปลายเดือน / สิ้นเดือน, the 11th to the 25th). Other files are a single bill or invoice, a payslip,
or HR papers. Read the page exactly as written; never invent, never add numbers up yourself.

page_kind:
- cover: the summary page of the half (สรุปค่าใช้จ่าย / รายการสรุป), totals per part. cover_parts: expense = the expense list total
  (ค่าใช้จ่าย / รายการเบิก), fuel = the fuel total (ค่าน้ำมัน), advance = the mid-month advance total (เบิกกลางเดือน / เบิกล่วงหน้า);
  0 for a part not on the cover (all 0 on pages that are not covers). written_total = the cover's grand total.
- expense: a list of petty-cash spending (รายการเบิก): date, what, amount, who took it.
- fuel: a list of fuel fills (เบิกค่าน้ำมัน): date, truck plate / number, amount, driver.
- fuel_table: a TYPED table of fuel per truck for the month (a summary of the fuel lists).
- advance: a list of employees taking their mid-month advance (เบิกกลางเดือน), one line per person (or its typed total); also the signed
  copy of that list with deductions (หักส่วนกลาง, ค่าความผิด).
- memo: an HR or office paper with no money to book: memo (บันทึกข้อความ), warning letter, resignation, attendance / fingerprint record,
  per-person daily work grid, sack report.
- payslip: a pay slip or a salary sheet.
- bill: a bill, receipt, tax invoice or statement from a shop or a utility.
- other: anything else.

lines: ONLY for expense, fuel, advance and fuel_table pages (empty for the others), every line top to bottom.
- date: dates are Buddhist Era, often short ("26/9", "1-10-69", "3/10/69"): 69 = 2569 BE = 2026; a day/month without a year takes the
  year of the page header. Return YYYY-MM-DD Gregorian. A line with no date has the date of the line above. Fuel table lines: null.
- amount: the baht amount as a number. A stray mark like ",1000" is 1000. null if unreadable.
- plate / truck_number: on fuel, tyre and repair lines, as written ("84-4607 (31)" -> plate "84-4607", truck_number "31"); else null.
- account: the best account from this chart (code - name - what such lines say):
{chart}
  Fuel lines are 6130. Advance-list lines are 1500.
- unsure: true when the amount, date, plate or number could not be read with confidence.
date_from / date_to: the period the page covers as written in its header or title (or its first and last line dates).
written_total: the total written on the page as written (often red ink at the bottom of the amount column); null if none.
branch_hints: copy exactly any text that names the BRANCH the paper belongs to ("สาขาเมืองสระบุรี", "[SRB]", "สาขาแก่งคอย", "KK").
  Not the company name สระบุรีวารีเทพ on its own, not a province in an address.
employee_codes: every employee code on the page (SB-FR076, SB-PD042, SB-KK012 ...).
work_rows: ONLY on a per-person table with a วันทำงาน column (days) and OT / ค่าเที่ยว columns -- "บันทึกการทำงาน" (often together with
  เบิกกลางเดือน). One row per person, top to bottom: code, name, days, ot, trip as WRITTEN (handwritten digits; "" for a blank cell;
  a word such as "เบี้ยขยัน" or "-" in a cell goes to note, not to the number). Such a page is still page_kind advance when it has the
  advance column. Every other page: [].
bill: filled ONLY for page_kind bill (on other pages: every text "", total null, account 7900): party = who issued it; doc_no;
  date (YYYY-MM-DD); period_month = YYYY-MM of the month the bill is FOR when stated (ประจำเดือน / รอบการใช้), else "";
  total = the amount to pay; what = what it is for, short Thai; account = best account.
summary: one short Thai line saying what the page is (e.g. "หนังสือเตือน 21 ต.ค. 69 — ตีห้า (SB-FR068)").
person: the people a memo / payslip is about, or ""."""


def _system_prompt() -> str:
    return PROMPT.replace("{chart}", "\n".join(f"  {c} - {n} - {t}" for c, n, t in CHART))


# ------------------------------------------------------------------ the client (shared with the bill readers)
def _client():
    from app.extraction import _client_lazy
    return _client_lazy()


def _check_enabled() -> None:
    if not settings.inbox_ai_enabled:
        raise InboxAIError("การอ่านด้วย AI ปิดอยู่ (ตั้งค่า INBOX_AI_ENABLED)")
    if not settings.anthropic_api_key:
        raise InboxAIError("ยังไม่ได้ตั้งค่า ANTHROPIC_API_KEY — อ่านด้วย AI ไม่ได้")


def _usage_of(r) -> dict:
    u = getattr(r, "usage", None)
    if u is None:
        return {"input_tokens": 0, "output_tokens": 0}
    total_in = (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
    return {"input_tokens": int(total_in), "output_tokens": int(getattr(u, "output_tokens", 0) or 0)}


def read_page(jpeg: bytes, n: int, total: int) -> tuple[dict, list[dict]]:
    """One page image -> (the page read, the usage of every call made for it). Retried once when the answer does not parse."""
    from app.extraction import FALLBACK, MODEL, _content_block
    usages, last = [], None
    for attempt in range(1, MAX_PARSE_ATTEMPTS + 1):
        try:
            r = _client().beta.messages.create(
                model=MODEL, max_tokens=MAX_OUTPUT_TOKENS, **FALLBACK,
                system=[{"type": "text", "text": _system_prompt(), "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": [_content_block(jpeg, "image/jpeg"),
                                                       {"type": "text", "text": f"Page {n} of {total}. Read this page."}]}],
                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": PAGE_SCHEMA}})
            usages.append(_usage_of(r))
            if r.stop_reason == "refusal":
                raise ValueError("the model declined this page")
            if r.stop_reason == "max_tokens":
                raise ValueError(f"the answer was cut off at {MAX_OUTPUT_TOKENS} tokens")
            out = json.loads(next(b.text for b in r.content if b.type == "text"))
            if out.get("page_kind") not in PAGE_KINDS or not isinstance(out.get("lines"), list):
                raise ValueError("no page_kind / lines")
            return out, usages
        except Exception as exc:                       # noqa: BLE001 -- retried once, then the page is reported, the file goes on
            last = exc
            logger.warning("inbox AI page %d attempt %d failed: %r", n, attempt, exc)
            if _is_bad_request(exc):                   # the request itself is wrong: a retry gets the same 400, and so would every page
                err = InboxAIError(f"AI ไม่รับคำขอ ({exc})")
                err.usages, err.fatal = usages, True
                raise err from exc
    err = InboxAIError(f"อ่านหน้า {n} ไม่สำเร็จ ({last})")
    err.usages = usages                                # a failed page still cost tokens: they are counted
    raise err from last


def _is_bad_request(exc: Exception) -> bool:
    try:
        import anthropic
        return isinstance(exc, anthropic.BadRequestError)
    except Exception:                                  # noqa: BLE001
        return False


# ------------------------------------------------------------------ pages of the file
def is_pdf(name: str, data: bytes) -> bool:
    return data[:5] == b"%PDF-" or name.lower().endswith(".pdf")


def is_image(name: str, data: bytes) -> bool:
    return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[8:12] == b"WEBP"
            or name.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp", "heic"))


def page_count(name: str, data: bytes) -> int:
    """What the button says: PDF pages, 1 for a photo, 0 for a file the AI cannot read (a workbook, a Word file ...)."""
    if is_pdf(name, data):
        import pymupdf
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                return doc.page_count
        except Exception:                              # noqa: BLE001
            return 0
    return 1 if is_image(name, data) else 0


def _fit(img: bytes) -> bytes:
    """A page image no bigger than LONG_SIDE on its long side, as JPEG (a 40 MB scan PDF renders huge pages; a phone photo is 12 MP)."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(img)))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if max(im.size) <= LONG_SIDE and img[:3] == b"\xff\xd8\xff" and len(img) < 3_500_000:
        return img
    im.thumbnail((LONG_SIDE, LONG_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue()


def page_images(name: str, data: bytes) -> list[bytes]:
    if is_pdf(name, data):
        n = page_count(name, data)
        if n == 0:
            raise InboxAIError("เปิด PDF นี้ไม่ได้")
        if n > MAX_PAGES:
            raise InboxAIError(f"PDF มี {n} หน้า — อ่านด้วย AI ได้ไม่เกิน {MAX_PAGES} หน้าต่อไฟล์ (แยกไฟล์ก่อน)")
        return [_fit(p) for p in ic.pdf_page_images(data, dpi=150)]
    if is_image(name, data):
        try:
            return [_fit(data)]
        except Exception as exc:                       # noqa: BLE001 -- e.g. HEIC without a decoder
            raise InboxAIError("เปิดรูปนี้ไม่ได้ (ส่งเป็น JPG / PNG / PDF)") from exc
    raise InboxAIError("AI อ่านได้เฉพาะไฟล์ PDF หรือรูปภาพ")


def read_pages(images: list[bytes], on_progress: Optional[Callable] = None) -> tuple[list[dict], dict]:
    """Every page read, at most PARALLEL at a time. A page that fails twice is kept as page_kind 'error' (the file still reads)."""
    total, done, lock = len(images), [0], threading.Lock()
    usage = {"model": None, "calls": 0, "input_tokens": 0, "output_tokens": 0}

    stop = threading.Event()                          # set by a request the API refuses outright: the pages not started are skipped

    def work(item):
        n, jpeg = item
        if stop.is_set():
            return n, {"page_kind": "error", "error": "ไม่ได้อ่าน (หยุดเพราะ AI ไม่รับคำขอ)", "lines": []}, []
        try:
            out, used = read_page(jpeg, n, total)
        except InboxAIError as exc:
            if getattr(exc, "fatal", False):
                stop.set()
            out, used = {"page_kind": "error", "error": str(exc), "lines": [], "fatal": getattr(exc, "fatal", False)}, getattr(exc, "usages", [])
        with lock:
            done[0] += 1
            if on_progress:
                try:
                    on_progress(done[0], total)
                except Exception:                      # noqa: BLE001 -- progress is a nicety, never the reason a read fails
                    pass
        return n, out, used

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        results = list(pool.map(work, enumerate(images, 1)))
    from app.extraction import MODEL
    usage["model"] = MODEL
    reads = []
    for n, out, used in results:
        out["n"] = n
        reads.append(out)
        for u in used:
            usage["calls"] += 1
            usage["input_tokens"] += u["input_tokens"]
            usage["output_tokens"] += u["output_tokens"]
    fatal = next((r for r in reads if r.get("fatal")), None)
    if fatal:                                          # nothing useful was read: the engine marks the file failed, with the reason
        err = InboxAIError(fatal["error"])
        err.usage, err.partial_pages = usage, [r for r in reads if r.get("page_kind") != "error"]
        raise err
    return reads, usage


# ------------------------------------------------------------------ the entry point the engine calls
def run(file_row: dict, data: bytes, on_progress: Optional[Callable] = None) -> dict:
    """file_row: the inbox_files row ({"id", "file_name", "choices" ...}). Reads every page with Claude and returns the PREVIEW
    (inbox_common contract, kind "scan"). Raises InboxAIError (Thai) when AI is switched off or the file cannot be read at all.
    on_progress(done, total) is called after each page."""
    _check_enabled()
    name = file_row.get("file_name") or file_row.get("name") or ""
    images = page_images(name, data)
    reads, usage = read_pages(images, on_progress)
    stored, flags = {}, []
    if file_row.get("id") and is_expense_document(reads):
        from app import acc_docs
        try:
            stored = acc_docs.store_page_images(f"inbox/{file_row['id']}", {i: img for i, img in enumerate(images, 1)})
        except Exception as exc:                       # noqa: BLE001 -- the books still book; the review screen shows no picture
            logger.warning("inbox AI: storing page images failed: %r", exc)
            flags.append("เก็บรูปหน้าเอกสารไม่สำเร็จ — หน้าตรวจจะไม่มีรูป")
    try:
        preview = from_pages(reads, name, dict(file_row.get("choices") or {}), usage=usage, images=stored)
    except Exception as exc:                           # noqa: BLE001 -- the pages were read and paid for: hand them back with the error
        logger.exception("inbox AI: building the preview failed")
        err = InboxAIError(f"อ่านหน้าเอกสารได้แล้ว แต่สร้างรายการไม่สำเร็จ ({exc})")
        err.usage, err.partial_pages = usage, reads
        raise err from exc
    preview["flags"] = flags + preview["flags"]
    return preview


def apply_choices(preview: dict, choices: dict) -> dict:
    """The owner answered a conflict: rebuild the preview from the stored page reads (no new API call). The owner's line edits made on
    ตรวจเอกสารก่อนลงบัญชี (preview["line_edits"]) are applied again on the rebuild, so a choice or a re-read never loses them
    (owner, 28 Nov 2026)."""
    if not preview.get("ai_pages"):
        return preview
    return from_pages(preview["ai_pages"], preview.get("file_name") or "", dict(choices or {}),
                      usage=preview.get("ai_usage"), images=preview.get("ai_images") or {}, line_edits=preview.get("line_edits"))


# ------------------------------------------------------------------ hints from the file name (a hint, never a decision)
_SHORT_MONTHS = ["มกรา", "กุมภา", "มีนา", "เมษา", "พฤษภา", "มิถุนา", "กรกฎา", "สิงหา", "กันยา", "ตุลา", "พฤศจิกา", "ธันวา"]


def branch_of_text(text: str) -> Optional[str]:
    t = re.sub(r"สระบุรีวารีเทพ|จ\.\s*สระบุรี|จังหวัดสระบุรี", "", text or "")
    if re.search(r"แก่งคอย|(?<![A-Za-z-])KK(?![A-Za-z])", t):
        return "แก่งคอย"
    if re.search(r"สระบุรี|SRB", t):
        return "สระบุรี"
    return None


def name_hints(name: str) -> dict:
    """{"half", "month", "branch"} from the file name alone ('10-กลางเดือนตุลา69.pdf' -> mid, 2026-10). Calendar month = company month
    here (the month a half is named after)."""
    half = "mid" if "กลางเดือน" in name else ("end" if re.search(r"ปลายเดือน|สิ้นเดือน", name) else None)
    month = ic.thai_month_in(name)
    if not month:
        for i, short in enumerate(_SHORT_MONTHS):
            m = re.search(short + r"[ก-๙.]{0,6}\s*(25\d\d|\d\d)(?!\d)", name)
            if m:
                y = int(m.group(1))
                month = f"{(y if y > 2400 else 2500 + y) - 543}-{i + 1:02d}"
                break
    return {"half": half, "month": month, "branch": branch_of_text(name)}


def hint_text(h: dict) -> str:
    parts = [HALF_TH[h["half"]] if h.get("half") else None, ic.month_label(h["month"]) if h.get("month") else None, h.get("branch")]
    parts = [p for p in parts if p]
    return ("คำใบ้จากชื่อไฟล์ (ยังไม่ได้ตรวจจากเนื้อหา): " + " · ".join(parts)) if parts else ""


# ------------------------------------------------------------------ small helpers
def _day(s) -> Optional[date]:
    if not s:
        return None
    m = re.match(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})", str(s))
    if m:
        y = int(m.group(1))
        y = y - 543 if y > 2400 else y
        try:
            d = date(y, int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return d if date(2020, 1, 1) <= d <= date(2040, 12, 31) else None
    return ic.be_date(str(s))


def half_of(d: date) -> str:
    return "mid" if d.day >= 26 or d.day <= 10 else "end"


def half_range(month: str, half: str) -> tuple[date, date]:
    y, m = int(month[:4]), int(month[5:7])
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    return (date(py, pm, 26), date(y, m, 10)) if half == "mid" else (date(y, m, 11), date(y, m, 25))


def month_range(month: str) -> tuple[date, date]:
    return half_range(month, "mid")[0], half_range(month, "end")[1]


def batch_month(batch: str) -> Optional[str]:
    """The company month an acc_docs batch belongs to: the script's '2026-11-saraburi' or the drop box's 'docs:2026-11:สระบุรี:mid'."""
    m = re.match(r"^(?:docs:)?(\d{4}-\d{2})(?=[-:])", str(batch or ""))
    return m.group(1) if m else None


def norm_plate(s) -> str:
    return re.sub(r"[\s\-.]", "", str(s or ""))


def match_truck(plate, number, vehicles: list[dict], branch: Optional[str]) -> tuple[Optional[dict], bool]:
    """(vehicle, by_plate). A plate decides; a number alone only within the branch (numbers repeat across branches)."""
    p = norm_plate(plate)
    if p:
        for v in vehicles:
            if norm_plate(v.get("plate_number")) == p:
                return v, True
        digits = re.sub(r"\D", "", p)
        if len(digits) >= 4:
            hits = [v for v in vehicles if re.sub(r"\D", "", v.get("plate_number") or "").endswith(digits[-4:])]
            mine = [v for v in hits if v.get("branch") == branch]
            if len(mine) == 1:
                return mine[0], True
            if len(hits) == 1:
                return hits[0], True
    n = str(number or "").strip().lstrip("0")
    if n.isdigit():
        hits = [v for v in vehicles if str(v.get("vehicle_number") or "").lstrip("0") == n]
        mine = [v for v in hits if v.get("branch") == branch]
        if len(mine) == 1:
            return mine[0], False
        if len(hits) == 1 and not branch:
            return hits[0], False
    return None, False


def role_of(account: str, unsure: bool = False) -> str:
    if account in ("1500", "1510"):
        return "advance"
    if account == "2400":
        return "pays_bill"
    if account in WAGES:
        return "wages"
    if account == "7600":
        return "tax"
    if account == "7900" and unsure:
        return "unknown"
    return "cost"


def pick(signals: list[dict], what: str, choices: dict, label: Callable[[str], str], title: str) -> tuple[Optional[str], list[dict]]:
    """month_conflict for any key (branch, half): the owner's choice, else the weighted vote; disagreement -> a conflict."""
    if choices.get(what):
        return choices[what], []
    best, against = ic.vote(signals, what)
    if not against:
        return best, []
    seen = []
    for v in [best] + [s["value"] for s in against]:
        if v not in seen:
            seen.append(v)
    src = lambda v: ", ".join(s["source"] for s in signals if s.get("what") == what and s.get("value") == v)   # noqa: E731
    return best, [{"key": what, "message": f"{title}ไม่ตรงกัน: " + " แต่ ".join(f"{src(v)} บอก {label(v)}" for v in seen),
                   "options": [{"value": v, "label": f"{label(v)} ({src(v)})"} for v in seen], "default": best}]


def _ask(what: str, message: str, options: list[tuple[str, str]], default: Optional[str] = None) -> dict:
    return {"key": what, "message": message, "options": [{"value": v, "label": lb} for v, lb in options], "default": default or options[0][0]}


def _recent_months(n: int = 3) -> list[str]:
    cur = ic.company_month(date.today())
    return [ic.shift_month(cur, -i) for i in range(n)]


def _majority(values: list) -> Optional[str]:
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else None


def _db_ready() -> bool:
    from app import supabase
    return supabase.configured()


def _vehicles() -> list[dict]:
    if not _db_ready():
        return []
    from app import supabase
    try:
        return supabase.select_all("vehicles", {"select": "id,vehicle_number,plate_number,branch", "order": "id.asc"})
    except Exception:                                  # noqa: BLE001
        return []


def is_expense_document(reads: list[dict]) -> bool:
    kinds = {r.get("page_kind") for r in reads}
    return bool(kinds & {"expense", "fuel", "advance", "cover", "fuel_table"})


# ------------------------------------------------------------------ the preview
def _cash_fuel_lines(month: str, branch: str, half: str) -> list[dict]:
    """The bookkeeper's checked, non-duplicate cash-fuel lines of this branch and half ([] = she has not done it: the paper books its fuel)."""
    if not _db_ready():
        return []
    from app import supabase
    try:
        return supabase.select_all("cash_fuel_lines", {"select": "day,amount,vehicle_id", "month": f"eq.{month}", "branch": f"eq.{branch}",
                                                       "batch": f"eq.{1 if half == 'mid' else 2}", "checked": "eq.true", "dup_of": "is.null",
                                                       "order": "id.asc"})
    except Exception:                                  # noqa: BLE001 -- no answer: book the paper's fuel as before
        return []


def skip_cash_fuel(batch: dict, done: list[dict], vehicles: Optional[list] = None) -> list:
    """Mark the paper's fuel lines (6130) that ARE on the cash-fuel page as not booked (acc_docs still counts them in the page total):
    first the same day and amount; then the same day and truck (an amount misread: her checked 500 read as 600 off the paper -- her
    page wins, owner 28 Nov 2026); then the same amount on another day (a date misread: 31/10 read as 3/10). A fuel line she does not have
    (no truck written, so she left it out) stays booked from the paper -- nothing is lost. Returns the lines marked."""
    left = [{"day": str(r["day"])[:10], "amount": round(float(r["amount"]), 2), "vehicle_id": r.get("vehicle_id")} for r in done]

    def truck(ln) -> Optional[str]:
        """The id of the line's truck: the one the owner picked (opt["vid"]), else the plate written on the paper."""
        opt = ln[5] or {}
        if opt.get("vid"):
            return opt["vid"]
        plate = opt.get("v")
        v = match_truck(plate, None, vehicles, batch.get("branch"))[0] if plate and vehicles else None
        return (v or {}).get("id")
    lines = [(p, ln) for p in batch.get("pages") or [] for ln in p.get("lines") or [] if ln[3] == "6130"]
    month = batch.get("month") or ""
    out = []

    def take(pred) -> Optional[dict]:
        for i, r in enumerate(left):
            if pred(r):
                return left.pop(i)
        return None

    # passes, most certain first, each over every line still open: a paper line takes the line of ITS truck before another truck's
    # line of the same day and amount (28 Nov 2026: two truck-52 fills dated 21/11 on the paper took truck 38's 21/11 line, and
    # truck 38's own paper line was then booked again)
    same = lambda vid, r: bool(vid and r["vehicle_id"] and r["vehicle_id"] == vid)
    clash = lambda vid, r: bool(vid and r["vehicle_id"] and r["vehicle_id"] != vid)
    passes = [
        lambda d, amt, vid: (lambda r: r["day"] == d and r["amount"] == amt and same(vid, r)) if d else None,
        lambda d, amt, vid: (lambda r: r["day"] == d and r["amount"] == amt and not clash(vid, r)) if d else None,
        lambda d, amt, vid: (lambda r: r["day"] == d and same(vid, r)) if d and vid else None,          # an amount misread: hers wins
        # the same amount on another day -- never her line of ANOTHER truck (both trucks known and different: a different fill-up;
        # e.g. the owner deleted the paper line that was hers, 28 Nov 2026)
        lambda d, amt, vid: (lambda r: r["amount"] == amt and not clash(vid, r)),
        lambda d, amt, vid: (lambda r: r["day"] == d and r["amount"] == amt) if d else None,              # a plate misread
    ]
    rest = lines
    for i, mk in enumerate(passes):
        still = []
        for p, ln in rest:
            pred = mk(_line_day(ln[0], month), round(float(ln[2]), 2), truck(ln))
            hit = take(pred) if pred else None
            if hit:
                if i == 2:
                    ln[5]["cf_amount"] = hit["amount"]
                out.append((p, ln))
            else:
                still.append((p, ln))
        rest = still
    for p, ln in out:
        ln[5]["skip"] = True
        ln[5].setdefault("page", p.get("page"))
    return [ln for _, ln in out]


def _line_day(dm: str, month: str) -> Optional[str]:
    """'31/10' in company month '2026-11' -> '2026-10-31' (a day on or after the 26th is the month before)."""
    try:
        d, m = (int(x) for x in str(dm).split("/")[:2])
        y = int(month[:4])
        if month[5:7] == "01" and m == 12:
            y -= 1
        return date(y, m, d).isoformat()
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ ตรวจเอกสารก่อนลงบัญชี: the owner's own fixes, and where each line goes
# (owner, 28 Nov 2026: "before I press ลงบัญชี I want one place to see each page beside its lines, fix them straight away, and see
# where every line will go"). The fixes live in preview["line_edits"]:
#   {"edits": {"p3:l2": {"date": "2026-11-02", "text": ..., "amount": ..., "account": ..., "vehicle_id": ..., "delete": true}},
#    "added": [{"id": "x1", "page": 3, "date": ..., "text": ..., "amount": ..., "account": ..., "vehicle_id": ...}]}
# keyed by the batch line number (p<page>:l<n>, the number acc_docs.build gives the line), which stays the same while the page reads
# (ai_pages) do: every rebuild (a choice, อ่านใหม่) applies them again; a fresh paid read starts without them.
TRUCK_ACCOUNTS = ("6100", "6110", "6120", "6130")
DEST_NAME = {"1500": "เงินเบิกล่วงหน้า", "1510": "เงินกู้พนักงาน"}
MAX_EDITS = 300
MAX_AMOUNT = 10_000_000
_EDIT_FIELDS = {"date", "text", "amount", "account", "vehicle_id", "delete"}
_KEY_RE = re.compile(r"^p(\d{1,3}):l(\d{1,4})$")
_ADDED_ID_RE = re.compile(r"^x[0-9a-z]{1,12}$")


def account_name(code: str) -> str:
    if code in DEST_NAME:
        return DEST_NAME[code]
    return next((re.sub(r"\s*\(ไม่ใช่ค่าใช้จ่าย\)\s*$", "", n) for c, n, _ in CHART if c == code), "")


def chart_list() -> list[list[str]]:
    """[[code, name], ...] for the account pick list of the check page (the same chart the AI picks from)."""
    return [[c, n] for c, n, _ in CHART]


def _vehicle_of(v, vehicles: list[dict], branch: Optional[str] = None, vid=None) -> Optional[dict]:
    """The vehicle a line's opt names, the way acc_docs.build finds it: the id the owner picked (opt["vid"]) first, then the plate,
    then the number within the branch."""
    if vid:
        hit = next((x for x in vehicles if x.get("id") == vid), None)
        if hit:
            return hit
    if not v:
        return None
    p = norm_plate(v)
    hit = next((x for x in vehicles if x.get("plate_number") and norm_plate(x["plate_number"]) == p), None)
    if hit:
        return hit
    nums = [x for x in vehicles if str(x.get("vehicle_number") or "") == str(v)]
    return next((x for x in nums if x.get("branch") == branch), nums[0] if nums else None)


def dest_text(account: str, vehicle: Optional[dict] = None, plate=None) -> str:
    """ไปที่ of a booked line: 'บัญชี 6130 น้ำมันเงินสด · รถ #5 (1ฒถ 2085)'. A truck is named by its number AND plate (numbers repeat
    across branches)."""
    out = f"บัญชี {account} {account_name(account)}".strip()
    if vehicle:
        num, pl = vehicle.get("vehicle_number"), vehicle.get("plate_number")
        out += " · รถ " + (f"#{num} ({pl})" if num and pl and str(num) != str(pl) else str(num or pl))
    elif plate:
        out += f" · รถ {plate} (ไม่พบในทะเบียนรถ)"
    return out


def _iso_dm(iso, lo: date, hi: date) -> Optional[str]:
    """'2026-11-02' -> '2/11' when it is inside [lo, hi], else None."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return f"{d.day}/{d.month}" if lo <= d <= hi else None


def _plate_for(vid, vehicles: list[dict]) -> Optional[str]:
    v = next((x for x in vehicles if x.get("id") == vid), None)
    return (v.get("plate_number") or v.get("vehicle_number")) if v else None


def apply_line_edits(batch: dict, line_edits: Optional[dict], vehicles: list[dict], month: str, half: str, reads: list[dict],
                     feed: str) -> tuple[list[dict], list[str]]:
    """The owner's fixes applied IN PLACE to the batch _batch built: a fixed line takes the new date / text / amount / account (and its
    party) / truck and is marked opt["edited"]; a deleted line leaves the batch (returned, so the page shows it struck through with
    เอาคืน); an added line goes to the end of its page (a page the AI found no money on becomes a money page again). A fixed line loses
    its (อ่านไม่ชัด) mark: the owner has looked at it. Lenient (the endpoint validated): a fix that no longer fits -- a date outside the
    half after the owner picked another month -- is left out with a flag, never moved. Returns (deleted lines, flags)."""
    le = line_edits if isinstance(line_edits, dict) else {}
    edits = le.get("edits") if isinstance(le.get("edits"), dict) else {}
    added = [a for a in (le.get("added") or []) if isinstance(a, dict)]
    if not edits and not added:
        return [], []
    lo, hi = half_range(month, half)
    flags: list[str] = []
    deleted: list[dict] = []

    def fix(ln, e: dict, key: str):
        dm, text, amount, acc, party, opt = ln
        opt = dict(opt or {})
        if e.get("date"):
            new = _iso_dm(e["date"], lo, hi)
            if new:
                dm = new
            else:
                flags.append(f"{key}: วันที่ที่แก้ไว้ ({e['date']}) อยู่นอกงวด — ใช้วันที่ที่อ่านได้")
        if isinstance(e.get("text"), str) and e["text"].strip():
            text = e["text"].strip()[:200]
        try:
            if e.get("amount") is not None and 0 < float(e["amount"]) < MAX_AMOUNT:
                amount = round(float(e["amount"]), 2)
        except (TypeError, ValueError):
            pass
        if e.get("account") in PARTY:
            acc, party = e["account"], PARTY[e["account"]]
        if "vehicle_id" in e:                          # the id itself decides the truck (a truck with no plate is named by its
            plate = _plate_for(e["vehicle_id"], vehicles) if e["vehicle_id"] else None     # number, which repeats across branches)
            if plate:
                opt["v"], opt["vid"] = plate, e["vehicle_id"]
            elif not e["vehicle_id"]:
                opt.pop("v", None)
                opt.pop("vid", None)
        text = text.replace(" (อ่านไม่ชัด)", "").replace("(อ่านไม่ชัด)", "").strip() or text
        opt["edited"] = True
        return (dm, text, amount, acc, party, opt)

    for p in batch["pages"]:
        kept = []
        for ln in p["lines"]:
            key = (ln[5] or {}).get("key")
            e = edits.get(key) if key else None
            if isinstance(e, dict) and e.get("delete"):
                deleted.append({"key": key, "page": p["page"], "date": _line_day(ln[0], month), "text": ln[1], "amount": ln[2],
                                "account": ln[3], "v": (ln[5] or {}).get("v"), "vid": (ln[5] or {}).get("vid")})
                continue
            kept.append(fix(ln, e, key) if isinstance(e, dict) and e else ln)
        p["lines"] = kept

    kinds = {r.get("n"): r for r in reads}
    for a in added:
        try:
            n = int(a.get("page"))
        except (TypeError, ValueError):
            continue
        r = kinds.get(n)
        if not r or r.get("page_kind") not in ("expense", "fuel") or not _ADDED_ID_RE.match(str(a.get("id") or "")):
            flags.append(f"บรรทัดที่เพิ่มเองไว้ในหน้า {n} ใส่ไม่ได้ (หน้านี้ไม่ใช่รายการเบิก) — ไม่ลงบัญชี")
            continue
        dm = _iso_dm(a.get("date"), lo, hi)
        try:
            amount = round(float(a.get("amount")), 2)
        except (TypeError, ValueError):
            amount = 0.0
        if not dm or not (0 < amount < MAX_AMOUNT):
            flags.append(f"บรรทัดที่เพิ่มเองไว้ในหน้า {n} ({a.get('text') or '-'}): วันที่อยู่นอกงวดหรือจำนวนเงินไม่ถูก — ไม่ลงบัญชี")
            continue
        p = next((x for x in batch["pages"] if x["page"] == n), None)
        if p is None:                                  # the AI found no money on this page (a memo now): it is a money page again
            batch["memos"] = [m for m in batch["memos"] if m["page"] != n]
            p = {"page": n, "file": "f", "file_page": n, "feed": feed, "kind": r["page_kind"], "title": r.get("title") or KIND_TH[r["page_kind"]],
                 "written_total": r.get("written_total"), "lines": [], "note": "อ่านด้วย AI"}
            batch["pages"].append(p)
            batch["pages"].sort(key=lambda x: x["page"])
        acc = a.get("account") if a.get("account") in PARTY else "7900"
        opt = {"key": f"p{n}:{a['id']}", "added": True, "edited": True}
        plate = _plate_for(a.get("vehicle_id"), vehicles) if a.get("vehicle_id") else None
        if plate:
            opt["v"], opt["vid"] = plate, a["vehicle_id"]
        p["lines"].append((dm, (str(a.get("text") or "").strip() or "-")[:200], amount, acc, PARTY[acc], opt))

    for p in [x for x in batch["pages"] if not x["lines"]]:    # every line deleted: the page is filed with no money
        batch["pages"].remove(p)
        batch["memos"].append({"page": p["page"], "file": "f", "file_page": p["page"], "kind": "other", "title": p.get("title"),
                               "summary": "ลบทุกบรรทัดของหน้านี้แล้ว — ไม่ลงบัญชี", "person": None})
    return deleted, flags


def clean_line_edits(preview: dict, line_edits, vehicles: list[dict]) -> dict:
    """The owner's fixes as the check page sends them, checked strictly (ValueError in Thai): every key a line of this file (or a line
    the owner added), dates inside the file's half (26-10 / 11-25 of its month), 0 < amount < 10 million, text 1-200 characters, an
    account of the chart (1500 / 1510 included), a truck that exists, lines added only to the money pages. A fix of an added line
    (key p<page>:<id>) is folded into that line. Returns {"edits", "added"}."""
    if not isinstance(line_edits, dict):
        raise ValueError("ข้อมูลที่แก้ไม่ถูกต้อง")
    edits_in = line_edits.get("edits") or {}
    added_in = line_edits.get("added") or []
    if not isinstance(edits_in, dict) or not isinstance(added_in, list):
        raise ValueError("ข้อมูลที่แก้ไม่ถูกต้อง")
    if len(edits_in) + len(added_in) > MAX_EDITS:
        raise ValueError(f"แก้ได้ครั้งละไม่เกิน {MAX_EDITS} บรรทัด")
    batch = preview.get("acc_docs_batch") or {}
    month, half = preview.get("month"), preview.get("half")
    if not batch or not month or half not in ("mid", "end"):
        raise ValueError("ไฟล์นี้ไม่ใช่เอกสารเบิกครึ่งเดือนที่อ่านด้วย AI — แก้รายการที่นี่ไม่ได้")
    lo, hi = half_range(month, half)
    known = {(ln[5] or {}).get("key") for p in batch.get("pages") or [] for ln in p.get("lines") or []}
    known |= set(((preview.get("line_edits") or {}).get("edits") or {}).keys())       # a line deleted earlier: it can be brought back
    known.discard(None)
    if not known:
        raise ValueError("ไฟล์นี้อ่านไว้ก่อนมีหน้าแก้รายการ — กด อ่านใหม่ (ไม่เสียค่า AI) แล้วแก้ได้")
    money_pages = {r.get("n") for r in preview.get("ai_pages") or [] if r.get("page_kind") in ("expense", "fuel")}
    vids = {v.get("id") for v in vehicles}
    span = f"{lo.strftime('%d/%m/%Y')} – {hi.strftime('%d/%m/%Y')}"

    def fields(e: dict, where: str, need: bool = False) -> dict:
        if not isinstance(e, dict):
            raise ValueError(f"{where}: ข้อมูลไม่ถูกต้อง")
        bad = set(e) - _EDIT_FIELDS - ({"id", "page"} if need else set())
        if bad:
            raise ValueError(f"{where}: แก้ช่อง {', '.join(sorted(bad))} ไม่ได้")
        out: dict = {}
        if need:
            for f in ("date", "text", "amount", "account"):
                if e.get(f) in (None, ""):
                    raise ValueError(f"{where}: ใส่วันที่ รายการ จำนวนเงิน และบัญชีให้ครบ")
        if e.get("date") not in (None, ""):
            if not _iso_dm(e["date"], date(2000, 1, 1), date(2100, 1, 1)):
                raise ValueError(f"{where}: วันที่ไม่ถูกต้อง")
            if not _iso_dm(e["date"], lo, hi):
                raise ValueError(f"{where}: วันที่ต้องอยู่ในงวดของไฟล์นี้ ({span})")
            out["date"] = str(e["date"])
        if e.get("text") is not None:
            t = str(e["text"]).strip()
            if not 1 <= len(t) <= 200:
                raise ValueError(f"{where}: รายการต้องยาว 1–200 ตัวอักษร")
            out["text"] = t
        if e.get("amount") not in (None, ""):
            try:
                a = round(float(str(e["amount"]).replace(",", "")), 2)
            except ValueError:
                raise ValueError(f"{where}: จำนวนเงินไม่ถูกต้อง")
            if not 0 < a < MAX_AMOUNT:
                raise ValueError(f"{where}: จำนวนเงินต้องมากกว่า 0 และน้อยกว่า 10 ล้าน")
            out["amount"] = a
        if e.get("account") not in (None, ""):
            if str(e["account"]) not in PARTY:
                raise ValueError(f"{where}: ไม่มีบัญชี {e['account']} ในผังบัญชี")
            out["account"] = str(e["account"])
        if "vehicle_id" in e:
            if e["vehicle_id"] and e["vehicle_id"] not in vids:
                raise ValueError(f"{where}: ไม่พบรถคันนี้")
            out["vehicle_id"] = e["vehicle_id"] or None
        if e.get("delete"):
            out["delete"] = True
        return out

    added, ids = [], set()
    for a in added_in:
        if not isinstance(a, dict):
            raise ValueError("บรรทัดที่เพิ่ม: ข้อมูลไม่ถูกต้อง")
        aid = str(a.get("id") or "")
        if not _ADDED_ID_RE.match(aid) or aid in ids:
            raise ValueError("บรรทัดที่เพิ่ม: รหัสบรรทัดไม่ถูกต้อง")
        try:
            n = int(a.get("page"))
        except (TypeError, ValueError):
            n = None
        if n not in money_pages:
            raise ValueError(f"เพิ่มบรรทัดได้เฉพาะหน้ารายการเบิก / เบิกค่าน้ำมัน (หน้า {a.get('page')})")
        ids.add(aid)
        clean = fields({k: v for k, v in a.items() if k not in ("id", "page", "delete")}, f"บรรทัดที่เพิ่มในหน้า {n}", need=True)
        added.append({"id": aid, "page": n, **clean})
    edits = {}
    for key, e in edits_in.items():
        key = str(key)
        mine = next((a for a in added if key == f"p{a['page']}:{a['id']}"), None)
        if mine is not None:                           # a fix of a line the owner added: folded into it
            ch = fields(e, f"บรรทัดที่เพิ่มในหน้า {mine['page']}")
            if ch.pop("delete", None):
                added.remove(mine)
            else:
                mine.update(ch)
            continue
        if key not in known or not _KEY_RE.match(key):
            raise ValueError(f"ไม่พบบรรทัด {key} ในไฟล์นี้ — เปิดหน้าตรวจใหม่")
        ch = fields(e, f"หน้า {_KEY_RE.match(key).group(1)} บรรทัด {_KEY_RE.match(key).group(2)}")
        if ch:
            edits[key] = ch
    return {"edits": edits, "added": added}


def from_pages(reads: list[dict], name: str, choices: dict, usage: Optional[dict] = None, images: Optional[dict] = None,
               line_edits: Optional[dict] = None) -> dict:
    """The page reads -> the PREVIEW. Reads the database (vehicles, earlier documents, existing entries), never writes.
    line_edits: the owner's fixes to the lines of an expense document (apply_line_edits); a fresh paid read passes none (its line
    numbers may differ)."""
    reads = sorted(reads, key=lambda r: r.get("n") or 0)
    vehicles = _vehicles()
    if is_expense_document(reads):
        out = _expense_preview(reads, name, choices, vehicles, line_edits)
    elif any(r.get("page_kind") == "bill" and _is_bill(r.get("bill")) for r in reads):
        out = _bill_preview(reads, name, choices, vehicles)
    else:
        out = _store_preview(reads, name, choices)
    errors = [r for r in reads if r.get("page_kind") == "error"]
    if errors:
        out["flags"].insert(0, f"อ่านไม่สำเร็จ {len(errors)} หน้า: " + ", ".join(str(r["n"]) for r in errors) + " — ตรวจหน้าเหล่านี้เอง")
    out.update({"needs_ai": False, "pages": len(reads), "ai_usage": usage, "ai": True, "ai_pages": reads, "ai_images": images or {},
                "file_name": name, "page_kinds": [{"page": r.get("n"), "kind": r.get("page_kind"), "kind_th": KIND_TH.get(r.get("page_kind"), ""),
                                                   "title": r.get("title") or r.get("error") or ""} for r in reads]})
    return out


def _branch_signals(reads: list[dict], vehicles: list[dict], hints: dict) -> list[dict]:
    sig = []
    on_cover = [branch_of_text(h) for r in reads if r.get("page_kind") == "cover" for h in r.get("branch_hints") or []]
    on_pages = [branch_of_text(h) for r in reads if r.get("page_kind") != "cover" for h in r.get("branch_hints") or []]
    if _majority(on_cover):
        sig.append({"what": "branch", "source": "หน้าปก", "value": _majority(on_cover), "weight": 3})
    elif _majority(on_pages):
        sig.append({"what": "branch", "source": "ข้อความบนเอกสาร", "value": _majority(on_pages), "weight": 3})
    codes = []
    for r in reads:
        for c in r.get("employee_codes") or []:
            m = re.search(r"SB\s*-?\s*(FR|PD|KK)", str(c).upper())
            if m:
                codes.append("แก่งคอย" if m.group(1) == "KK" else "สระบุรี")
    if _majority(codes):
        sig.append({"what": "branch", "source": f"รหัสพนักงาน ({len(codes)})", "value": _majority(codes), "weight": 2})
    trucks = []
    for r in reads:
        if r.get("page_kind") not in ("fuel", "expense", "fuel_table"):
            continue
        for ln in r.get("lines") or []:
            v, by_plate = match_truck(ln.get("plate"), None, vehicles, None)
            if v and by_plate and v.get("branch"):
                trucks.append(v["branch"])
    if _majority(trucks):
        sig.append({"what": "branch", "source": f"ทะเบียนรถ ({len(trucks)} คัน-ครั้ง)", "value": _majority(trucks), "weight": 2})
    if hints.get("branch"):
        sig.append({"what": "branch", "source": "ชื่อไฟล์", "value": hints["branch"], "weight": 0.5})
    return sig


def _expense_preview(reads: list[dict], name: str, choices: dict, vehicles: list[dict], line_edits: Optional[dict] = None) -> dict:
    from app import acc_docs
    hints = name_hints(name)
    pv = ic.empty_preview(KIND, "เอกสารเบิกครึ่งเดือน (อ่านด้วย AI)")
    if line_edits:
        pv["line_edits"] = line_edits
    covers = [r for r in reads if r.get("page_kind") == "cover"]
    line_days = [d for r in reads if r.get("page_kind") in ("expense", "fuel") for ln in r.get("lines") or [] for d in [_day(ln.get("date"))] if d]
    signals = []
    for c in covers:
        d = _day(c.get("date_to"))
        if d:
            signals.append({"what": "half", "source": f"หน้าปก (หน้า {c['n']})", "value": half_of(d), "weight": 3})
            signals.append({"what": "month", "source": f"หน้าปก (หน้า {c['n']})", "value": ic.company_month(d), "weight": 3})
    if line_days:
        signals.append({"what": "half", "source": f"วันที่ในรายการ ({len(line_days)} บรรทัด)", "value": _majority([half_of(d) for d in line_days]), "weight": 2})
        signals.append({"what": "month", "source": f"วันที่ในรายการ ({len(line_days)} บรรทัด)", "value": _majority([ic.company_month(d) for d in line_days]), "weight": 2})
    if hints["half"]:
        signals.append({"what": "half", "source": "ชื่อไฟล์", "value": hints["half"], "weight": 0.5})
    if hints["month"]:
        signals.append({"what": "month", "source": "ชื่อไฟล์", "value": hints["month"], "weight": 0.5})
    signals += _branch_signals(reads, vehicles, hints)
    pv["signals"] = signals

    month, conflicts = ic.month_conflict(signals, choices)
    if not month and not conflicts:
        month = choices.get("month")
        if not month:
            opts = _recent_months()
            conflicts.append(_ask("month", "ไม่พบวันที่ในเอกสาร — เลือกเดือน", [(m, ic.month_label(m)) for m in opts]))
            month = opts[0]
    else:
        month = month or next((c["default"] for c in conflicts if c["key"] == "month"), None)
    half, cf = pick(signals, "half", choices, lambda v: HALF_TH.get(v, v), "ครึ่งเดือน")
    conflicts += cf
    if not half:
        conflicts.append(_ask("half", "บอกไม่ได้ว่าเป็นเอกสารกลางเดือนหรือปลายเดือน — เลือก", [("mid", HALF_TH["mid"]), ("end", HALF_TH["end"])]))
        half = "mid"
    branch, cf = pick(signals, "branch", choices, lambda v: v, "สาขา")
    conflicts += cf
    if not branch:
        conflicts.append(_ask("branch", "ไม่พบสาขาในเอกสาร — เลือกสาขา", [(b, b) for b in ic.BRANCHES]))
        branch = ic.BRANCHES[0]
    pv.update({"month": month, "branch": branch, "half": half})
    feed = "midmonth" if half == "mid" else "endmonth"
    key = f"docs:{month}:{branch}:{half}"
    pv["label"] = f"เอกสารเบิก{'กลางเดือน' if half == 'mid' else 'ปลายเดือน'} {branch} {ic.month_label(month)} (อ่านด้วย AI)"

    batch, flags = _batch(reads, name, key, month, branch, half, feed, vehicles)
    pv["flags"] += flags
    # the owner's fixes (ตรวจเอกสารก่อนลงบัญชี): after the month / half vote (a fixed date never moves the file), before the cash-fuel
    # match and the page totals (a fixed fuel amount is matched again; the totals add up what is left)
    deleted, eflags = apply_line_edits(batch, line_edits, vehicles, month, half, reads, feed)
    pv["flags"] += eflags
    done = _cash_fuel_lines(month, branch, half)
    skipped = skip_cash_fuel(batch, done, vehicles) if done else []
    if skipped:
        kept = [ln for p_ in batch.get("pages") or [] for ln in p_.get("lines") or [] if ln[3] == "6130" and not ln[5].get("skip")]
        pv["flags"].append(f"น้ำมันเงินสด {len(skipped)} รายการ ({sum(x[2] for x in skipped):,.2f} บาท) อยู่ในหน้าน้ำมันเงินสดแล้ว (คนทำบัญชีตรวจแล้ว) — ไม่ลงซ้ำจากเอกสารนี้"
                           + (f" · อีก {len(kept)} รายการ ({sum(x[2] for x in kept):,.2f} บาท) ไม่มีในหน้าน้ำมันเงินสด — ลงจากเอกสารนี้" if kept else ""))
    try:
        out = acc_docs.build(batch, vehicles)
    except acc_docs.BadBatch as exc:
        pv["flags"].append(f"สร้างรายการไม่ได้: {exc}")
        pv["conflicts"] = conflicts
        return pv
    pv["flags"] += out["problems"]
    pv["page_checks"] = [{"page": d["page"], "kind": d["kind"], "kind_th": KIND_TH.get(d["kind"], d["kind"]), "title": d.get("title"),
                          "written_total": d.get("written_total"), "read_total": d.get("read_total"), "status": d.get("status"),
                          "summary": d.get("summary"), "person": d.get("person")}
                         for d in sorted(out["docs"], key=lambda d: d["page"])]

    # Every row says where it goes (ไปที่, owner 28 Nov 2026): key = the line's stable key (p<page>:l<n>, or p<page>:<id> for a line
    # the owner added), dest = the Thai text, dest_kind book / cashfuel / noted / deleted. Lines not booked keep their text as read;
    # the reason is in dest, never glued onto the text.
    line_at = {(p["page"], i): ln for p in batch["pages"] for i, ln in enumerate(p["lines"], 1)}
    rows = []
    for e in out["entries"]:
        debit = e["lines"][0]
        unsure = "(อ่านไม่ชัด)" in (e.get("description") or "")
        m = re.search(r":p(\d+):l(\d+)$", e.get("source_id") or "")
        opt = ((line_at.get((int(m.group(1)), int(m.group(2)))) if m else None) or [None] * 6)[5] or {}
        veh = _vehicle_of(opt.get("v"), vehicles, branch, opt.get("vid"))
        rows.append({"date": e["entry_date"], "text": e["description"], "party": e["party"], "amount": float(debit["debit"]),
                     "role": role_of(debit["account"], unsure), "account": debit["account"], "flag": "อ่านไม่ชัด" if unsure else None,
                     "page": e["doc_page"], "branch": e["branch"], "key": opt.get("key"), "vehicle_id": debit.get("vehicle_id"),
                     "dest": dest_text(debit["account"], veh, opt.get("v")), "dest_kind": "book",
                     "edited": bool(opt.get("edited")), "added": bool(opt.get("added"))})
    for dm, text, amount, account, party, opt in skipped:
        her = f" ({opt['cf_amount']:,.2f})" if opt.get("cf_amount") not in (None, amount) else ""
        veh = _vehicle_of(opt.get("v"), vehicles, branch, opt.get("vid"))
        unsure = "(อ่านไม่ชัด)" in (text or "")
        rows.append({"date": _line_day(dm, month), "text": text, "party": party, "amount": 0.0, "read_amount": float(amount), "role": "info",
                     "account": account, "flag": "อ่านไม่ชัด" if unsure else None, "page": opt.get("page"), "key": opt.get("key"),
                     "vehicle_id": (veh or {}).get("id"), "dest": f"มีแล้วในน้ำมันเงินสด{her}", "dest_kind": "cashfuel",
                     "edited": bool(opt.get("edited")), "added": bool(opt.get("added"))})
    for x in deleted:
        veh = _vehicle_of(x.get("v"), vehicles, branch, x.get("vid"))
        rows.append({"date": x["date"], "text": x["text"], "party": PARTY.get(x["account"]), "amount": 0.0, "read_amount": float(x["amount"]),
                     "role": "info", "account": x["account"], "flag": None, "page": x["page"], "key": x["key"],
                     "vehicle_id": (veh or {}).get("id"), "dest": "ลบแล้ว · ไม่ลง", "dest_kind": "deleted", "edited": True, "added": False})
    for d in out["docs"]:
        if d["kind"] in ("memo", "other") or (d["kind"] == "cover" and d.get("read_total") is None):
            rows.append({"date": d.get("date_from"), "text": f"หน้า {d['page']}: {d.get('title') or ''}" + (f" — {d['summary']}" if d.get("summary") else ""),
                         "party": d.get("person"), "amount": 0.0, "role": "info", "account": None, "flag": None, "page": d["page"],
                         "key": f"p{d['page']}:page", "dest": "ไม่ลงบัญชี · ข้อมูลประกอบ" + (f" · {d['person']}" if d.get("person") else ""),
                         "dest_kind": "noted"})
    written = [c["written_total"] for c in batch["covers"] if c.get("written_total") is not None]
    if not written:
        written = [p["written_total"] for p in batch["pages"] if p.get("written_total") is not None]
    pv["batches"] = [{"key": key, "feed": feed, "source": feed, "branch": branch, "month": month,
                      "title": f"{'เอกสารเบิกกลางเดือน' if half == 'mid' else 'เอกสารเบิกปลายเดือน'} {branch} {ic.month_label(month)}",
                      "rows": rows, "file_total": round(sum(written), 2) if written else None,
                      "rows_total": round(sum(r["amount"] for r in rows), 2), "entries": out["entries"]}]
    pv["acc_docs_batch"] = batch

    # The same half already in the books (the script's month batch, or this half from another file): the owner chooses.
    old = _earlier_docs(key, branch, feed, month, half, name)
    if old:
        edited = sum(o["edited"] for o in old)
        where = ", ".join(f"{o['batch']} ({o['file_name'] or '-'}, {len(o['pages'])} หน้า)" for o in old)
        choice = choices.get("replace_old")
        one = next((o for o in old if choice == f"replace:{o['batch']}"), None) if str(choice or "").startswith("replace:") else None
        if choice == "replace" or one:
            take = [one] if one else old
            pv["replace_docs"] = [{"batch": o["batch"], "pages": o["pages"]} for o in take]
            pv["flags"].append(f"จะลบของเดิม {sum(len(o['pages']) for o in take)} หน้า ("
                               + ", ".join(f"{o['batch']} ({o['file_name'] or '-'}, {len(o['pages'])} หน้า)" for o in take) + ") แล้วลงไฟล์นี้แทน")
            if one and len(old) > 1:
                pv["flags"].append("ของเดิมชุดอื่นของครึ่งเดือนนี้ยังอยู่ในบัญชี — ตรวจว่าไม่นับซ้ำ")
        elif choice == "keep":
            pv["store_only"] = True
            pv["note"] = f"เก็บไว้กับเดือน ไม่ลงบัญชี — ใช้ของเดิมที่ลงไว้แล้ว ({where})"
        else:
            def warn_edited(n):
                return f" (มี {n} หน้าที่แก้บนหน้าตรวจแล้ว — ที่แก้จะหาย)" if n else ""
            opts = [("replace", ("แทนที่ของเดิมทั้งหมดด้วยไฟล์นี้" if len(old) > 1 else "แทนที่ของเดิมด้วยไฟล์นี้") + warn_edited(edited))]
            if len(old) > 1:                           # one option per earlier batch too: the owner may want to take over only one of them
                opts += [(f"replace:{o['batch']}", f"แทนที่เฉพาะ {o['batch']} ({o['file_name'] or '-'}, {len(o['pages'])} หน้า)" + warn_edited(o["edited"]))
                         for o in old]
            opts.append(("keep", "เก็บของเดิม — ไฟล์นี้ไม่ลงบัญชี"))
            conflicts.append(_ask("replace_old", f"{HALF_TH[half]} {branch} {ic.month_label(month)} ลงบัญชีไว้แล้ว: {where}", opts, default="keep"))
    pv["conflicts"] = conflicts
    if not pv["note"]:
        pv["note"] = (f"{len(reads)} หน้า: " + ", ".join(f"{KIND_TH.get(k, k)} {n}" for k, n in Counter(r.get('page_kind') for r in reads).most_common())
                      + " — ทุกหน้าที่มีเงินเทียบยอดที่เขียนไว้กับยอดที่อ่านได้")
    worked = sum(len(r.get("work_rows") or []) for r in reads)
    if worked:                                       # the work record's days / OT / ค่าเที่ยว: used by the pay page, not booked here
        pv["note"] = (pv.get("note") or "") + f" · บันทึกการทำงาน {worked} คน (วัน / OT / ค่าเที่ยว) ใช้ในหน้าคำนวณเงินเดือน"
    return pv


def _batch(reads: list[dict], name: str, key: str, month: str, branch: str, half: str, feed: str, vehicles: list[dict]) -> tuple[dict, list[str]]:
    """The page reads -> the BATCH acc_docs.build takes (the same shape as data/acc_docs/*.py), and what the reader noticed."""
    h_from, h_to = half_range(month, half)
    m_from, m_to = month_range(month)
    batch = {"batch": key, "month": month, "branch": branch, "files": {"f": name}, "covers": [], "pages": [], "memos": [], "fuel_table": None}
    flags: list[str] = []
    ft = None

    def memo(r, kind="other", title=None, summary=None):
        batch["memos"].append({"page": r["n"], "file": "f", "file_page": r["n"], "kind": kind, "title": title or r.get("title") or KIND_TH.get(r.get("page_kind"), ""),
                               "summary": summary if summary is not None else (r.get("summary") or r.get("error")), "person": r.get("person") or None})

    for r in reads:
        k, n = r.get("page_kind"), r["n"]
        if k == "error":
            memo(r, "other", "อ่านหน้านี้ไม่สำเร็จ")
        elif k == "cover":
            parts = {p: float(v) for p, v in (r.get("cover_parts") or {}).items() if v}       # 0 = part not on the cover
            total = r.get("written_total") if r.get("written_total") is not None else (sum(parts.values()) if parts else None)
            if not parts or total is None:
                memo(r, "cover")
                flags.append(f"หน้า {n}: ปกไม่มียอดแยกส่วน — ไม่ได้ใช้เทียบ")
                continue
            batch["covers"].append({"page": n, "file": "f", "file_page": n, "feed": feed, "title": r.get("title") or "สรุปค่าใช้จ่าย",
                                    "from": (_day(r.get("date_from")) or h_from).isoformat(), "to": (_day(r.get("date_to")) or h_to).isoformat(),
                                    "written_total": float(total), "parts": parts})
        elif k in ("expense", "fuel"):
            lines, last, unsure = [], None, 0
            for i, ln in enumerate(r.get("lines") or [], 1):
                d = _day(ln.get("date")) or last or _day(r.get("date_from"))
                amt = ln.get("amount")
                if amt is None or float(amt) <= 0:
                    flags.append(f"หน้า {n} บรรทัด {i}: อ่านจำนวนเงินไม่ได้ ({ln.get('text') or '-'}) — ไม่ลงบัญชี")
                    continue
                if not d:
                    d = h_from
                    flags.append(f"หน้า {n} บรรทัด {i}: ไม่มีวันที่ — ใช้ {d.strftime('%d/%m/%Y')}")
                elif not (m_from - timedelta(days=5) <= d <= m_to + timedelta(days=5)):
                    flags.append(f"หน้า {n} บรรทัด {i}: วันที่ {d.strftime('%d/%m/%Y')} อยู่นอกเดือน {ic.month_label(month)} — ตรวจ")
                last = d
                acc = ln.get("account") if ln.get("account") in PARTY else "7900"
                if k == "fuel":
                    acc = "6130"
                opt = {"key": f"p{n}:l{len(lines) + 1}"}     # the batch line number acc_docs.build gives it (the owner's edits use it)
                if ln.get("plate") or ln.get("truck_number"):
                    v, _ = match_truck(ln.get("plate"), ln.get("truck_number"), vehicles, branch)
                    opt["v"] = v["plate_number"] if v else (ln.get("plate") or ln.get("truck_number"))
                text = (ln.get("text") or "-").strip()
                if ln.get("unsure"):
                    unsure += 1
                    if "(อ่านไม่ชัด)" not in text:
                        text += " (อ่านไม่ชัด)"
                lines.append((f"{d.day}/{d.month}", text, round(float(amt), 2), acc, PARTY[acc], opt))
            if not lines:
                memo(r, "other")
                flags.append(f"หน้า {n} ({KIND_TH[k]}): ไม่มีบรรทัดที่มีจำนวนเงิน")
                continue
            if unsure:
                flags.append(f"หน้า {n}: อ่านไม่ชัด {unsure} บรรทัด")
            batch["pages"].append({"page": n, "file": "f", "file_page": n, "feed": feed, "kind": k, "title": r.get("title") or KIND_TH[k],
                                   "written_total": r.get("written_total"), "lines": lines, "note": "อ่านด้วย AI"})
        elif k == "advance":
            if half == "end":
                # the end-of-month file carries the SIGNED copies of the mid-month advance list: the mid-month file already booked it
                memo(r, "other", (r.get("title") or "เบิกกลางเดือน") + " (ฉบับเซ็นรับ)",
                     (r.get("summary") or "") + " — ยอดเบิกลงไว้แล้วจากเอกสารกลางเดือน ไม่ลงซ้ำ")
                continue
            amounts = [float(ln["amount"]) for ln in r.get("lines") or [] if ln.get("amount") and float(ln["amount"]) > 0]
            amount = round(sum(amounts), 2)
            if amount <= 0:
                if not r.get("written_total"):
                    memo(r, "other")
                    flags.append(f"หน้า {n} (เบิกกลางเดือน): อ่านยอดไม่ได้ — ไม่ลงบัญชี")
                    continue
                amount = float(r["written_total"])
                flags.append(f"หน้า {n} (เบิกกลางเดือน): อ่านรายชื่อไม่ได้ ใช้ยอดที่เขียนไว้ {amount:,.0f}")
            days = [d for ln in r.get("lines") or [] for d in [_day(ln.get("date"))] if d]
            d = Counter(days).most_common(1)[0][0] if days else (_day(r.get("date_to")) or h_to)
            who = f" ({len(amounts)} คน)" if len(amounts) > 1 else ""
            batch["pages"].append({"page": n, "file": "f", "file_page": n, "feed": feed, "kind": "advance", "title": r.get("title") or "เบิกกลางเดือน",
                                   "written_total": r.get("written_total"),
                                   "lines": [(f"{d.day}/{d.month}", f"{r.get('title') or 'เบิกกลางเดือน'}{who}", amount, "1500", PARTY["1500"],
                                              {"key": f"p{n}:l1"})],
                                   "note": "อ่านด้วย AI: รวมทุกชื่อในหน้าเป็นบรรทัดเดียว"})
        elif k == "fuel_table":
            by_truck = (ft or {}).get("by_truck", {})
            for ln in r.get("lines") or []:
                if not ln.get("amount"):
                    continue
                v, _ = match_truck(ln.get("plate"), ln.get("truck_number"), vehicles, branch)
                t = str(v["vehicle_number"]) if v else str(ln.get("truck_number") or ln.get("plate") or ln.get("text") or "?")
                by_truck[t] = round(by_truck.get(t, 0) + float(ln["amount"]), 2)
            if ft is None:
                ft = {"page": n, "file": "f", "file_page": n, "title": r.get("title") or KIND_TH["fuel_table"], "written_total": r.get("written_total"),
                      "by_truck": by_truck}
            else:
                ft["by_truck"] = by_truck
                if r.get("written_total") is not None:
                    ft["written_total"] = max(float(ft["written_total"] or 0), float(r["written_total"]))      # the grand total is on the last page
                memo(r, "other", (r.get("title") or KIND_TH["fuel_table"]) + " (ต่อ)", f"รวมอยู่ในหน้า {ft['page']}")
        else:
            memo(r, "memo" if k in ("memo", "payslip") else "other")
    if ft:
        if ft["written_total"] is None:
            ft["written_total"] = round(sum(ft["by_truck"].values()), 2)
        batch["fuel_table"] = ft
    return batch, flags


def _earlier_docs(key: str, branch: str, feed: str, month: str, half: str, name: str) -> list[dict]:
    """Pages already in acc_docs for this branch and half from another batch (the script's month batch) or from another file under the
    same key: [{"batch", "file_name", "pages", "edited"}]. Every page of those files is taken over (their memos too).
    Only batches of the SAME company month count (the script's '<month>-<branch>' batch, or a drop-box 'docs:<month>:...' key): a page
    of next month's papers whose dates spill into this half (a mid-month list starting on the 8th) is never taken over -- 'replace'
    would delete next month's half from the books (review 26 Nov 2026)."""
    if not _db_ready():
        return []
    from app import supabase
    h_from, h_to = half_range(month, half)
    try:
        hits = supabase.select_all("acc_docs", {"select": "batch,file_name", "branch": f"eq.{branch}", "feed": f"eq.{feed}",
                                                "date_from": f"lte.{h_to.isoformat()}", "date_to": f"gte.{h_from.isoformat()}", "order": "batch.asc"})
        same = supabase.select("acc_docs", {"select": "batch,file_name", "batch": f"eq.{key}", "limit": "1"})
    except Exception:                                  # noqa: BLE001 -- no acc_docs yet: nothing to replace
        return []
    files = {(h["batch"], h.get("file_name")) for h in hits if h["batch"] != key and batch_month(h["batch"]) == month}
    files |= {(s["batch"], s.get("file_name")) for s in same if s.get("file_name") != name}
    out = []
    for b, fn in sorted(files, key=lambda x: (x[0], x[1] or "")):
        params = {"select": "page,edited_at", "batch": f"eq.{b}", "order": "page.asc"}
        params["file_name"] = f"eq.{fn}" if fn else "is.null"
        rows = supabase.select_all("acc_docs", params)
        if rows:
            out.append({"batch": b, "file_name": fn, "pages": [r["page"] for r in rows], "edited": sum(1 for r in rows if r.get("edited_at"))})
    return out


# ------------------------------------------------------------------ a single bill / invoice / statement
def _bill_preview(reads: list[dict], name: str, choices: dict, vehicles: list[dict]) -> dict:
    hints = name_hints(name)
    pv = ic.empty_preview(KIND, "บิล / ใบแจ้งหนี้ (อ่านด้วย AI)")
    bills: dict = {}
    for r in reads:
        b = r.get("bill")
        if r.get("page_kind") != "bill" or not _is_bill(b):
            continue
        k = (re.sub(r"\s", "", b.get("party") or ""), (b.get("doc_no") or "").strip() or str(b.get("total")))
        if k in bills:                                 # the same bill on two pages: one bill
            bills[k]["pages"].append(r["n"])
            for f in ("total", "date", "period_month", "what", "doc_no"):
                bills[k][f] = bills[k].get(f) or b.get(f)
        else:
            bills[k] = {**b, "pages": [r["n"]]}
    bills_l = list(bills.values())
    signals = []
    for b in bills_l:
        pm = b.get("period_month") or ""
        if re.match(r"^\d{4}-\d{2}$", pm):
            y = int(pm[:4])
            pm = f"{y - 543 if y > 2400 else y}{pm[4:]}"
            signals.append({"what": "month", "source": f"เดือนที่ระบุบนบิล ({b.get('party') or '-'})", "value": pm, "weight": 3})
        elif _day(b.get("date")):
            signals.append({"what": "month", "source": f"วันที่บนบิล ({b.get('party') or '-'})", "value": ic.company_month(_day(b["date"])), "weight": 2})
    if hints["month"]:
        signals.append({"what": "month", "source": "ชื่อไฟล์", "value": hints["month"], "weight": 0.5})
    signals += _branch_signals(reads, vehicles, hints)
    pv["signals"] = signals
    month, conflicts = ic.month_conflict(signals, choices)
    if not month and not conflicts:
        opts = _recent_months()
        conflicts.append(_ask("month", "ไม่พบเดือนบนบิล — เลือกเดือน", [(m, ic.month_label(m)) for m in opts]))
        month = opts[0]
    month = month or next(c["default"] for c in conflicts if c["key"] == "month")
    branch, cf = pick(signals, "branch", choices, lambda v: v, "สาขา")
    conflicts += cf
    if not branch:
        conflicts.append(_ask("branch", "ไม่พบสาขาบนบิล — เลือกสาขา", [(b, b) for b in ic.BRANCHES]))
        branch = ic.BRANCHES[0]
    pv.update({"month": month, "branch": branch, "conflicts": conflicts})
    m_from, m_to = month_range(month)
    h = hashlib.sha1("|".join(sorted(f"{b.get('party')}|{b.get('doc_no')}|{b.get('total')}" for b in bills_l)).encode()).hexdigest()[:8]
    key = f"bill:{month}:{branch}:{h}"
    entries, rows, feeds, pea = [], [], set(), 0
    for i, b in enumerate(bills_l, 1):
        total = b.get("total")
        if not total or float(total) <= 0:
            pv["flags"].append(f"หน้า {', '.join(map(str, b['pages']))}: อ่านยอดเงินบนบิลไม่ได้ — ไม่ลงบัญชี")
            continue
        total = round(float(total), 2)
        text = f"{b.get('party') or ''} {b.get('what') or ''}"
        feed, acc = next(((f, a) for rx, f, a in BILL_FEEDS if rx.search(text)), (None, b.get("account") if b.get("account") in PARTY else "7900"))
        if feed:
            feeds.add(feed)
        d = _day(b.get("date"))
        when = d if d and m_from <= d <= m_to else m_to
        desc = (b.get("what") or KIND_TH["bill"]) + (f" (บิลลงวันที่ {d.strftime('%d/%m/%Y')})" if d and d != when else "")
        e = {"entry_date": when.isoformat(), "branch": branch, "source": feed or "manual", "source_id": f"{key}:{i}", "party": (b.get("party") or PARTY[acc])[:100],
             "description": desc[:300], "doc_type": "bill", "doc_no": (b.get("doc_no") or None),
             "lines": [{"account": acc, "debit": total, "credit": 0.0, "branch": branch, "vehicle_id": None, "memo": None},
                       {"account": "2100", "debit": 0.0, "credit": total, "branch": branch, "vehicle_id": None, "memo": None}]}
        if feed == "electricity":
            # never booked from an AI read: the PEA reader books the bill as pea:<invoice> (elec_bills + the cheque that pays it); a
            # 'bill:' copy would be a second electricity cost the cheque never clears (review 26 Nov 2026)
            pea += 1
            rows.append({"date": e["entry_date"], "text": e["description"], "party": e["party"], "amount": total, "role": "info",
                         "account": acc, "flag": "ค่าไฟ PEA — ไม่ลงจากการอ่านด้วย AI", "page": b["pages"][0], "feed": feed})
            continue
        dup = _already_booked(acc, total, branch, m_from, m_to, bill=b, own=h)
        rows.append({"date": e["entry_date"], "text": e["description"], "party": e["party"], "amount": total, "role": role_of(acc),
                     "account": acc, "flag": dup, "page": b["pages"][0], "feed": feed})
        if dup:
            pv["flags"].append(f"{e['party']} {total:,.2f}: {dup}")
        else:
            entries.append(e)
    if pea:
        pv["flags"].append(PEA_NOTE)
    if rows and not entries:
        pv["store_only"] = True
        pv["note"] = PEA_NOTE if pea == len(rows) else "บิลนี้ลงบัญชีไว้แล้ว — เก็บไว้กับเดือนเป็นหลักฐาน ไม่ลงซ้ำ"
    else:
        pv["note"] = (f"บิล {len(rows)} ใบ — ลงเป็นค่าใช้จ่ายค้างจ่าย (เจ้าหนี้ 2100): เช็ค HQ รอบเดือนถัดไปที่จ่ายบิลนี้ (ยอดตรงภายใน 1 บาทและชื่อผู้รับคล้ายกัน หรือมีเลขที่บิลในหมายเหตุ)"
                      " จะเป็น 'จ่ายบิลที่มีอยู่แล้ว' ไม่ใช่ค่าใช้จ่ายซ้ำ"
                      " — ตรวจยอด หมวด และสาขาก่อนกดลงบัญชี")
    feed = next(iter(feeds)) if len(feeds) == 1 else None
    pv["label"] = "บิล / ใบแจ้งหนี้ (อ่านด้วย AI)" if not feed else f"บิล{_feed_th(feed)} (อ่านด้วย AI)"
    if entries or rows:
        pv["batches"] = [{"key": key, "feed": feed or "manual", "source": feed or "manual", "branch": branch, "month": month,
                          "title": pv["label"], "rows": rows,
                          # a bill left out (PEA, or already booked) is not a short read: no file total then, the flags say why
                          "file_total": round(sum(r["amount"] for r in rows), 2) if all(not r["flag"] for r in rows) else None,
                          "rows_total": round(sum(r["amount"] for r in rows if not r["flag"]), 2), "entries": entries}]
    return pv


PEA_NOTE = ("ใบแจ้งค่าไฟ PEA — ไม่ลงบัญชีจากการอ่านด้วย AI (กันค่าไฟนับซ้ำ): ให้วางไฟล์ PDF ใบแจ้งหนี้ที่ดาวน์โหลดจาก PEA ลงกล่องรับไฟล์แทน "
            "ตัวอ่าน PEA จะลงบิลและจับคู่กับเช็คที่จ่ายให้เอง — ไฟล์นี้เก็บไว้กับเดือนเป็นหลักฐาน")


def _is_bill(b) -> bool:
    return isinstance(b, dict) and bool(b.get("total") or (b.get("party") or "").strip())


def _feed_th(feed: str) -> str:
    from app.accounting import FEED_BY_KEY
    return FEED_BY_KEY.get(feed, {}).get("th", "")


def _own_entry(source_id, own: Optional[str]) -> bool:
    """An entry this same bill booked earlier ('bill:<month>:<branch>:<hash>:<n>', whatever month / branch it was booked under)."""
    return bool(own) and re.match(rf"^bill:[^:]+:[^:]+:{re.escape(own)}(?::|$)", str(source_id or "")) is not None


def _longest_common(a: str, b: str) -> int:
    from difflib import SequenceMatcher
    if not a or not b:
        return 0
    return SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b)).size


def alike_bill(bill: dict, entry: dict) -> bool:
    """Is this booked entry the same bill? The same document number, or a similar party / what (company words, spaces and tone
    marks ignored), or the same utility named on both (ชลประทาน / ประปา / การไฟฟ้า). An entry of the same account and amount alone
    is not enough: two different 1,200-baht purchases are two costs."""
    from app.acc_checks import norm_doc, norm_name, similar
    doc = norm_doc(bill.get("doc_no"))
    if doc and (doc == norm_doc(entry.get("doc_no")) or doc in re.sub(r"[^0-9A-Z]", "", str(entry.get("description") or "").upper())):
        return True
    mine = [bill.get("party"), bill.get("what")]
    theirs = [entry.get("party"), entry.get("description")]
    if any(similar(x, y) for x in mine for y in theirs):
        return True
    tb, te = " ".join(str(x or "") for x in mine), " ".join(str(x or "") for x in theirs)
    if any(rx.search(tb) and rx.search(te) for rx, _, _ in BILL_FEEDS):
        return True
    return _longest_common(norm_name(tb), norm_name(te)) >= 6


def _already_booked(account: str, amount: float, branch: str, start: date, end: date, bill: Optional[dict] = None,
                    own: Optional[str] = None) -> Optional[str]:
    """The same cost already in the books this month (e.g. irrigation water paid from petty cash, on the expense list): one source
    books a cost, the bill only proves it. `own` = this bill's hash: what this same bill booked before is never 'already booked'
    (re-reading or answering a question on a booked AI bill would otherwise turn it into stored-only and unbook it for good --
    review 26 Nov 2026). `bill` = the bill read: the entry must look like it (alike_bill), not just share the account and amount."""
    if not _db_ready():
        return None
    from app import supabase
    try:
        lines = supabase.select_all("acc_lines", {"select": "entry_id,debit,branch", "account": f"eq.{account}",
                                                  "and": f"(debit.gte.{amount - 1},debit.lte.{amount + 1})", "order": "id.asc"})
        ids = [ln["entry_id"] for ln in lines if (ln.get("branch") or branch) == branch]
        for i in range(0, len(ids), 100):
            hits = supabase.select("acc_entries", {"select": "id,entry_date,source,source_id,party,description,doc_no",
                                                   "id": f"in.({','.join(ids[i:i + 100])})",
                                                   "entry_date": f"gte.{start.isoformat()}", "and": f"(entry_date.lte.{end.isoformat()})"})
            for h in hits:
                if _own_entry(h.get("source_id"), own) or (bill is not None and not alike_bill(bill, h)):
                    continue
                return f"ลงไว้แล้ว {_day(h['entry_date']).strftime('%d/%m/%Y')} ({h.get('source')}: {h.get('description') or '-'}) — ไม่ลงซ้ำ"
    except Exception:                                  # noqa: BLE001 -- cannot check: let the owner see it unflagged
        return None
    return None


# ------------------------------------------------------------------ not for the books
def _store_preview(reads: list[dict], name: str, choices: dict) -> dict:
    hints = name_hints(name)
    kinds = Counter(r.get("page_kind") for r in reads)
    what = ", ".join(f"{KIND_TH.get(k, k)} {n} หน้า" for k, n in kinds.most_common())
    pv = ic.empty_preview(KIND, f"{KIND_TH.get(kinds.most_common(1)[0][0], 'เอกสาร')} (อ่านด้วย AI)", store_only=True)
    days = [d for r in reads for d in (_day(r.get("date_to")), _day(r.get("date_from"))) if d]
    signals = []
    if days:
        signals.append({"what": "month", "source": "วันที่บนเอกสาร", "value": _majority([ic.company_month(d) for d in days]), "weight": 2})
    if hints["month"]:
        signals.append({"what": "month", "source": "ชื่อไฟล์", "value": hints["month"], "weight": 0.5})
    month, conflicts = ic.month_conflict(signals, choices)
    pv.update({"signals": signals, "conflicts": conflicts, "month": month or next((c["default"] for c in conflicts), None)})
    summaries = [f"หน้า {r['n']}: {r.get('summary') or r.get('title') or ''}" for r in reads if r.get("page_kind") != "error"]
    pv["note"] = f"{what} — ไม่ใช่เอกสารลงบัญชี เก็บไว้กับเดือน"
    pv["contents"] = summaries
    return pv
