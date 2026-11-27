"""
คำนวณเงินเดือน (#payroll) -- step one: the CHECKLIST of what the pay calculation needs (owner, 26 Nov 2026).

HQ has always worked out everyone's pay; the owner now wants to do it here, following the workbook "แบบคำนวณเงินเดือน (เสนอ).xlsx".
Before any figure is calculated this page answers one question for a pay month (26th - 25th, named by the 25th): for every input of
the calculation and for every person, does IceBox already have this month's own value (ok), only a stand-in -- the figure on HQ's
latest pay sheet, the driver's days for his helper, a loan instalment on schedule but not yet marked "หักแล้ว" -- (carry), or nothing
to go on (miss)? It calculates no pay and it WRITES NOTHING: every function here only reads.

Three things the answer never does: call a missing answer "not needed" (a kind of input whose papers are not in carries a `note`
saying it is not known yet), promise a green tick that no upload can give (a kind IceBox cannot hold yet says so in `later`), or count
as "people to pay" someone who left earlier and only waits for savings (summary.left_before).

The owner's decisions of 27 Nov 2026 are built in: the workbook's written rules are "the primary rules for now" and are shown as they are
(RULES_*, the answer's "rules" and each person's "formula"); เบี้ยขยัน goes to drivers only; groups A, B, C are paid on sales AFTER the truck's
fuel, so their trucks' fuel is an input like ทวาย's; สะสม follows HQ's latest sheet (confirmed, no longer a stand-in) while ปกส. and เบิกล่วงหน้า
wait for him; สระบุรี OT is cash and stays out of pay; and แก่งคอย is set aside (HOLD_BRANCHES): its people stay listed, but out of every count.

Who is on the list: company-paid people (employees.pay_group <> 'hq') who worked any day of the period, plus people who left earlier
and still have savings to get back. Rules per person: employee_pay_rules (schema_v93.sql); the daily rate: employee_rates (app/rates.py).

Privacy: pay data rides on a menu and an API prefix of its own ("payroll", /api/payroll). Every select names its columns (SELECTS) --
never "*" on a people table, never a phone number, a bank account, an ID / passport number, a password, a PIN or a token.

The build functions are pure (plain rows in, the answer out) and unit-tested; the loaders at the bottom only read, side by side, each one
guarded so that a source that fails is named in "failed" instead of blanking the page.
"""
import logging
import re
import threading
import time
from datetime import date, timedelta
from typing import Callable, Optional

import httpx

from app import loans as loan_rules
from app.accounting import period_bounds, period_of
from app.rates import rate_on

logger = logging.getLogger("payroll")

NEEDS = "schema_v93.sql"
CACHE_SECONDS = 60
RETRY_SECONDS = 10                                # an answer with a failed part is kept only this long
MAX_ROWS = 80                                     # lines listed under one kind of input
FOLD = 6                                          # this many people with the very same line become one line ("107 คน")
LOOK_BACK = 5                                     # HQ sheets this many months back can stand in for the deductions
BRANCHES = ["สระบุรี", "แก่งคอย"]
# owner, 27 Nov 2026: แก่งคอย is on hold for now (he will fill its OT in himself later). Its people stay on the list with "hold": true and
# their cells as they are, but they are in no count, their own files are only information and no decision is asked about them.
HOLD_BRANCHES = ("แก่งคอย",)
ROUTE_GROUPS = ("DRV-A", "DRV-B", "DRV-C", "DRV-D", "DRV-E")          # สระบุรี route drivers: days and sales come from ยอดขายรายวัน
NET_OF_FUEL_GROUPS = ("DRV-A", "DRV-B", "DRV-C")                      # commission on sales AFTER the truck's fuel (owner, 27 Nov 2026)
FUEL_GROUPS = NET_OF_FUEL_GROUPS + ("DRV-D",)                         # whose truck's fuel the calculation needs (ทวาย: off his ซอง)
DRIVER_GROUPS = ROUTE_GROUPS + ("DRV-F", "KK-DRV")                    # the only people who get เบี้ยขยัน (owner, 27 Nov 2026)
ROUTE_POSITION = "พนักงานขับรถสาย"                                   # the position ยอดขายรายวัน lists (app/driver_days.py POSITION)
# the workbook's groups (sheet กติกา)
GROUP_LABELS = {
    "DRV-A": "พขร. รถหลอดใหญ่ 120/วัน + 5%", "DRV-B": "พขร. หลอด + ซอง (สะอาด)", "DRV-C": "พขร. หลอด + ซอง (สำเริง)",
    "DRV-D": "พขร. ซอง (ทวาย)", "DRV-E": "พขร. รถปลีก-เล็ก 350/วัน + 2%", "DRV-F": "พขร. หน้าโรง (รายวัน)",
    "KK-DRV": "พขร. แก่งคอย (ค่าเที่ยว)", "KK-PROD": "ผลิต แก่งคอย (+OT)", "FAC-COM": "หน้าโรง / แพ็ค (+% ยอดขาย)",
    "DAY": "รายวัน (ผลิต / ช่าง / ออฟฟิศ / อื่นๆ)",
}
GROUP_ORDER = ["DRV-A", "DRV-B", "DRV-C", "DRV-D", "DRV-E", "DRV-F", "FAC-COM", "DAY", "KK-DRV", "KK-PROD"]      # the order the page lists them in
# ---- THE RULES, as data. Workbook "แบบคำนวณเงินเดือน (เสนอ).xlsx", sheets วิธีใช้ and กติกา; the owner on 27 Nov 2026: "keep the rules you wrote
# on the excel template, that's the primary for now; make sure it's visible and intuitive to see". Nothing here calculates: the texts are
# shown (the answer's "rules", each person's "formula") so the owner reads the very rule the next step will apply.
RULES_SOURCE = "แบบคำนวณเงินเดือน (เสนอ) · เจ้าของยืนยัน 27/11/2026"
BONUS_BAHT = 1000                                 # เบี้ยขยัน: days worked >= the days of the period, and not withheld
BAG_PRICE = 140                                   # ทวาย: his truck's fuel / 140 = the ซอง taken off before the baht per ซอง
# one line each; {start} {end} {days} {bonus} {bag_price} are filled in by build_rules
RULES_GENERAL = [
    "งวดเงินเดือน: วันที่ 26 ของเดือนก่อน ถึง 25 ของเดือนนี้ (งวดนี้ {start} ถึง {end} · {days} วัน)",
    "รวมรับ = ค่าแรง + ค่าคอม + ค่าซอง + เบี้ยขยัน + OT",
    "เบี้ยขยัน {bonus} บาท: เฉพาะพนักงานขับรถ เมื่อมาทำงานครบ {days} วันของงวด และไม่ถูกงด",
    "พขร. กลุ่ม A, B, C: หักค่าน้ำมันรถออกจากยอดขายก่อนคิดค่าคอม · ทวาย: ค่าซองคิดจาก จำนวนซอง − ค่าน้ำมัน ÷ {bag_price}",
    "พนักงานผลิตที่ออกรถด้วย (2 อัตรา): วันออกรถอย่างเดียวจ่ายอัตราที่สอง วันทำงานในโรงงานจ่ายอัตราปกติ",
    "OT สระบุรี จ่ายเป็นเงินสดทุกวันจากเงินสดย่อย ไม่รวมในเงินเดือน",
    "วันที่มาสแกนแต่ไม่ได้ทำงาน ไม่จ่าย",
    "สะสม หักตามใบสรุปเงินเดือน HQ ล่าสุด",
    "คืนเงินสะสม / จ่ายค้าง ไม่ใช่รายได้ของเดือน: ไม่นับในรวมรับ แต่รวมในจ่ายจริง",
]
# one more line while a branch is set aside. Savings owed to someone of that branch who left are money to pay back, not part of what
# is set aside: they stay counted, and the line says so.
RULE_HOLD = "{hold}: เจ้าของจะใส่ OT เองภายหลัง ยังไม่นับในตัวเลขของหน้านี้ (คืนเงินสะสมของคนที่ลาออกยังนับตามปกติ)"
# a group's rule, part by part (what it pays, how). {rate} {pct} {bag} {ot} take the person's own numbers -- or the group's, when
# everyone in it has the same one. Every rule starts with ค่าแรง = days x the daily rate (wage_words). A part that needs a % is left
# out for a person who has none when the group is in OPTIONAL (แพ็ค: "+ % ถ้ามี").
OT_RULE = "ชม. OT × {ot} บาท"
GROUP_RULES = {
    "DRV-A": [("ค่าคอม", "(ยอดขาย − ค่าน้ำมันรถ) × {pct}")],
    "DRV-B": [("ค่าคอม", "(ยอดขายหลอด − ค่าน้ำมันรถ) × {pct}"), ("ค่าซอง", "จำนวนซอง × {bag} บาท")],
    # the workbook writes "ยอดขาย × 3.5%" for C; his ซอง are paid apart (per ซอง) and HQ worked the % out on the หลอด part, which is what the
    # ยอดขาย cell holds -- so the rule says หลอด, as B's does, and build_decisions asks the owner to confirm it
    "DRV-C": [("ค่าคอม", "(ยอดขายหลอด − ค่าน้ำมันรถ) × {pct}"), ("ค่าซอง", "จำนวนซอง × {bag} บาท")],
    "DRV-D": [("ค่าคอม", "ยอดขายหลอด × {pct}"), ("ค่าซอง", "(จำนวนซอง − ค่าน้ำมัน ÷ " + str(BAG_PRICE) + ") × {bag} บาท")],
    "DRV-E": [("ค่าคอม", "ยอดขาย × {pct}")],
    "DRV-F": [("", "ไม่มีค่าคอม")],
    "KK-DRV": [("ค่าเที่ยว", "กรอกเอง")],
    "KK-PROD": [("OT", OT_RULE)],
    "FAC-COM": [("ค่าคอม", "ยอดขาย × {pct}")],
    "DAY": [],
}
OPTIONAL = {"FAC-COM": "(+ % ของยอดขาย ถ้ามี)"}
# the workbook's own numbers (sheet กติกา), used for a group nobody on the list is in
GROUP_DEFAULTS = {"DRV-A": {"rate": 120, "pct": 5}, "DRV-B": {"rate": 120, "pct": 5, "bag": 10}, "DRV-C": {"rate": 203, "pct": 3.5, "bag": 3},
                  "DRV-D": {"rate": 368, "pct": 2, "bag": 3}, "DRV-E": {"rate": 350, "pct": 2}, "KK-PROD": {"ot": 40}}
# one per input of the calculation, in the workbook's column order (sheet คำนวณ: F วันทำงาน, G ยอดขาย, H ซอง, I น้ำมัน, J OT, K ค่าเที่ยว,
# L งดเบี้ยขยัน ... X สะสม, Y เบิก, Z เงินกู้ + AB ทำบัตร, AA ส่วนกลาง, AC เงินขาด / ความผิด, AD อุปกรณ์ / อื่นๆ, AE ปกส., AH คืนเงินสะสม):
# key, title, short title, where it comes from, where to fix it (link, label), and `later` -- what IceBox cannot hold yet, so no upload
# or button on this page can turn the line green (the next step of this job)
KINDS = [
    ("group", "กลุ่มคิดเงิน", "กลุ่ม", "กติกาคิดเงินรายคน (จากแผ่น อัตรา ของแบบคำนวณ)", None, None, ""),
    ("rate", "อัตรา/วัน", "อัตรา", "ค่าแรงต่อวัน ในหน้า รายชื่อพนักงาน", "#employees", "รายชื่อพนักงาน", ""),
    ("days", "วันทำงาน", "วัน", "รถสาย: ยอดขายรายวัน · คนอื่น: สแกนนิ้ว / ใบสรุปวันหยุด", "#attendance", "เวลาทำงาน",
     "คนที่ไม่ใช่รถสาย: ยังไม่มีที่สรุปวันทำงานในระบบ"),
    ("sales", "ยอดขาย", "ยอดขาย", "รถสาย: ยอดขายรายวัน (เงินที่ได้ของวันที่ขับเอง)", "#driver-days", "ยอดขายรายวัน", ""),
    ("bags", "จำนวนซอง", "ซอง", "รถสาย: ยอดขายรายวัน (จำนวนซองของวันที่ขับเอง)", "#driver-days", "ยอดขายรายวัน", ""),
    ("fuel", "ค่าน้ำมัน", "น้ำมัน", "ใบแจ้งหนี้บัตรน้ำมัน + น้ำมันเงินสด ของรถสาย กลุ่ม A, B, C และทวาย", "#accounts-in", "ลงข้อมูล / แนบไฟล์", ""),
    ("ot", "ชม. OT", "OT", "ใบสรุปสิ้นเดือนแก่งคอย / สแกนนิ้ว", "#attendance", "เวลาทำงาน", "ยังไม่มีที่ลงชั่วโมง OT รายคนในระบบ"),
    ("trip", "ค่าเที่ยว", "เที่ยว", "ตารางเที่ยว แก่งคอย", None, None, "ยังไม่มีที่ยืนยันค่าเที่ยวรายคนในระบบ"),
    ("nobonus", "งดเบี้ยขยัน", "งดเบี้ย", "เจ้าของตัดสินรายคน", None, None, "ยังไม่มีที่ลงในระบบ"),
    ("savings", "สะสม", "สะสม", "ใบสรุปเงินเดือน HQ ล่าสุด (เจ้าของยืนยัน 27/11/2026: หักตามใบ HQ)", None, None, ""),
    ("advance", "เบิกล่วงหน้า", "เบิก", "ใบเบิกกลางเดือน", "#accounts-in", "ลงข้อมูล / แนบไฟล์",
     "ระบบยังอ่านใบเบิกเป็นรายคนไม่ได้ — ใช้ยอดจากใบ HQ ล่าสุดแทน"),
    ("loan", "เงินกู้ / ทำบัตร", "กู้", "เงินกู้ในระบบ (ตารางผ่อนของเดือนนี้)", "#loans", "เงินกู้", ""),
    ("common", "ส่วนกลาง", "ส่วนกลาง", "เขียนมือในใบเบิกกลางเดือน", None, None, "ยังไม่มีที่ลงในระบบ"),
    ("penalty", "เงินขาด / ความผิด", "ปรับ", "ใบเตือนที่มีค่าปรับ ลงวันที่ในงวด", "#documents", "ใบเตือน / บันทึกข้อความ",
     "เงินขาดของคนขับ: ยังไม่มีที่ลงในระบบ"),
    ("other", "อุปกรณ์ / อื่นๆ", "อื่นๆ", "เขียนมือในเอกสารสิ้นเดือน", None, None, "ยังไม่มีที่ลงในระบบ"),
    ("sso", "ปกส.", "ปกส.", "ใบสรุปเงินเดือน HQ ล่าสุด (รอเจ้าของตรวจ)", None, None, "ยังไม่มีที่ยืนยันยอดรายคนในระบบ — ใช้ยอดจากใบ HQ ล่าสุดแทน"),
    ("refund", "คืนเงินสะสม", "คืนสะสม", "ยอดสะสมยกไปในใบ HQ ล่าสุด ของคนที่ลาออก", None, None, ""),
]
KEYS = [k[0] for k in KINDS]
TITLES = {k[0]: k[1] for k in KINDS}
TITLES_SOURCE = {k[0]: k[3] for k in KINDS}
# key, the code prefix of the people in the file, title, the branch the file is about
SCAN_GROUPS = [("scan_sb_pd", "SB-PD", "ไฟล์สแกนนิ้ว สระบุรี คนขับ (SB-PD)", "สระบุรี"), ("scan_sb_fr", "SB-FR", "ไฟล์สแกนนิ้ว สระบุรี โรงงาน (SB-FR)", "สระบุรี"),
               ("scan_kk", "SB-KK", "ไฟล์สแกนนิ้ว แก่งคอย (SB-KK)", "แก่งคอย")]
STATIONS = {"bangchak": "บางจาก", "pt": "PT"}
# บางจาก comes through the drop box as one file per station (app/inbox_r_fuel.py BC_STATIONS), booked "fuel:bangchak:<month>:<station>:<plate>";
# the older scripts booked both stations as one entry per plate, "fuel:bangchak:<month>:<plate>". An estimate is "...:est-<plate>".
BANGCHAK_STATIONS = ("NM", "ห้วยบง")
ESTIMATE = "est-"
HALVES = (1, 2)                                   # cash fuel comes in two halves of the pay month: 26th - 10th, 11th - 25th
WAITING = ("read", "needs_choice", "needs_ai", "ai_running")          # a drop-box file nobody has finished yet (schema_v92.sql)
NO_TABLE = ("PGRST205", "42P01", "HTTP 404")      # what PostgREST says when a table is not there

# every column this page reads, table by table (tests check: no "*", nothing private)
SELECTS = {
    "employees": "id,code,full_name,nickname,branch,position,status,start_date,end_date,pay_group,probation_end",
    "employee_pay_rules": "employee_id,pay_group,commission_pct,bag_rate,ot_rate,ot_auto_hours,bonus,rate2,rides_with,route_until",
    "employee_rates": "id,employee_id,daily_rate,effective_from,kind,review,review_note",
    "pay_prev": "month,code,employee_id,parts,deductions,refund,flags,sales:details->sales,savings_balance:details->savings_balance",
    "pay_old": "month,code,employee_id,refund,flags,savings_balance:details->savings_balance,bag:parts->bag,other:parts->other",
    "route_driver_days": "employee_id,day,qty,money,spare,money_parts",
    "route_driver_settings": "employee_id,money_split",
    "attendance_punches": "employee_id,punched_at",
    "attendance_uploads": "id,filename,period_start,period_end,unknown_codes,created_at",
    "employee_loans": "id,employee_id,kind,amount,loan_date,reason,repay_mode,repay_amount,repay_months,repay_percent,schedule,first_month,status",
    "employee_loan_entries": "id,loan_id,entry_date,kind,amount,pay_month,created_at",
    "acc_entries": "id,source_id,lines:acc_lines(vehicle_id,credit)",
    "cash_fuel_lines": "id,batch,amount,counted,dup_of,vehicle_id,page_id,page:cash_fuel_pages(confirmed)",
    "vehicles": "id,vehicle_number,plate_number,vehicle_class,branch,usage,main_driver_id,active",
    "drivers": "id,name",
    "fin_trip_tables": "id,employee_code,written_total,computed_total",
    "acc_docs": "batch,feed,branch,kind,page",
    "inbox_files": "id,kind,status,month,branches",
    "employee_documents": "id,employee_id,doc_date,status,penalty:content->penalty_amount",
}


class PayrollError(ValueError):
    """Refused, with a message shown as it is."""


# --------------------------------------------------------------------------------------------- small pure helpers
def _d(v) -> Optional[date]:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def dmy(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def dm(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}"


def my(month: str) -> str:
    """ "2026-10" -> "10/2026" """
    return f"{month[5:7]}/{month[:4]}"


def money(n) -> str:
    n = round(_f(n), 2)
    return f"{n:,.0f}" if n == int(n) else f"{n:,.2f}"


def num(n) -> str:
    """A rate, a % or a baht-per-ซอง inside a formula: 5.0 -> "5", 3.5 -> "3.5", 1000 -> "1,000"."""
    n = round(_f(n), 2)
    return f"{n:,.0f}" if n == int(n) else f"{n:,.2f}".rstrip("0")


def held(branch) -> bool:
    return branch in HOLD_BRANCHES


def hold_words() -> str:
    """ "แก่งคอยพักไว้ก่อน" -- how a line about a branch that is set aside starts."""
    return ", ".join(HOLD_BRANCHES) + "พักไว้ก่อน"


def runs(days: list[date]) -> str:
    """Dates in a tight place: three or more in a row become a range -- "22/11-25/11"; fewer are listed -- "24/11, 25/11"."""
    out, days = [], sorted(set(days))
    i = 0
    while i < len(days):
        j = i
        while j + 1 < len(days) and (days[j + 1] - days[j]).days == 1:
            j += 1
        out += [f"{dm(days[i])}-{dm(days[j])}"] if j - i >= 2 else [dm(d) for d in days[i:j + 1]]
        i = j + 1
    return ", ".join(out)


def _norm(name) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()


def who(e: dict) -> str:
    """A person as the page writes them: "SB-PD014 ศุภกร ... (ไมค์)" -- the nickname only when it says more than the name."""
    name, nick = _norm(e.get("full_name") or e.get("name")), _norm(e.get("nickname"))
    return f"{e.get('code') or ''} {name}".strip() + (f" ({nick})" if nick and nick != name.split(" ")[0] else "")


def truck_label(v: dict) -> str:
    """ "#27 84-2682 รถใหญ่ สระบุรี" -- the plate once, for a truck whose fleet number IS its plate ("83-2919 รถใหญ่ สระบุรี")."""
    number, plate = _norm(v.get("vehicle_number")), _norm(v.get("plate_number"))
    return " ".join(str(x) for x in (f"#{number}" if number and number != plate else "", plate, v.get("vehicle_class"), v.get("branch")) if x)


def cell(state: str = "na", value=None, text: str = "", note: str = "") -> dict:
    return {"state": state, "value": value, "text": text, "note": note}


NA = cell()


# --------------------------------------------------------------------------------------------- the pay month (pure)
def parse_month(value) -> str:
    m = str(value or "").strip()
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", m):
        raise PayrollError("เดือนไม่ถูกต้อง (ใช้ ค.ศ. เช่น 2026-11)")
    return m


def running_month(today: date) -> str:
    return period_of(today)["month"]


def default_month(today: date) -> str:
    """The pay month to open on: the one that contains (today - 6 days) -- for the first days after the 25th that is the month that
    just closed (26/11/2026 -> 2026-11), the one being paid; from the 2nd on, the running one."""
    return period_of(today - timedelta(days=6))["month"]


def add_month(month: str, n: int) -> str:
    t = int(month[:4]) * 12 + int(month[5:7]) - 1 + n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def months(today: date, count: int = 12) -> list[str]:
    """The running pay month and the `count` before it, newest first."""
    run = running_month(today)
    return [add_month(run, -i) for i in range(count + 1)]


# --------------------------------------------------------------------------------------------- who is on the list (pure)
def _said_refund(row: dict) -> bool:
    """HQ's sheet reader flagged the row's extra money as savings paid back (it sits in ซอง% / other, not in the refund column)."""
    return any("savings refund" in str(f) or "คืนสะสม" in str(f) or "คืนสะม" in str(f) for f in row.get("flags") or [])


def savings_owed(rows: list[dict]) -> Optional[dict]:
    """From a person's HQ pay rows: the latest savings balance HQ printed, and whether it was paid back on that sheet or a later one.
    {balance, month, refunded, unsure, last, said} -- None when no row carries a balance.
    Paid back = the refund column, or extra money (ซอง% / other) of at least the balance on a row the reader flagged as a savings
    refund (SB-PD046, 08/2026: 12,000 in ซอง% set off against his loan). unsure = a later sheet printed no balance and shows no
    refund either: the amount is then only the last one printed, to be checked, never a firm debt. said = HQ's own note about it."""
    rows = sorted(rows, key=lambda r: str(r.get("month") or ""), reverse=True)
    known = next((r for r in rows if r.get("savings_balance") is not None), None)
    if not known:
        return None
    balance = round(_f(known["savings_balance"]), 2)
    since = [r for r in rows if str(r.get("month") or "") >= str(known["month"])]
    refunded = any(_f(r.get("refund")) > 0 or (balance > 0 and _said_refund(r) and _f(r.get("bag")) + _f(r.get("other")) >= balance) for r in since)
    said = next((re.sub(r"^หมายเหตุในชื่อ:\s*", "", str(f)) for r in since for f in r.get("flags") or [] if str(f).startswith("หมายเหตุในชื่อ") and "สะสม" in str(f)), "")
    return {"balance": balance, "month": known["month"], "refunded": refunded, "unsure": not refunded and rows[0] is not known,
            "last": rows[0].get("month"), "said": said}


def roster(employees: list[dict], start: date, end: date, owed: Optional[dict] = None) -> tuple[list[dict], int]:
    """(the people of this pay month, how many are paid by HQ). Each: the employee row + `first` / `last` (their own days inside the
    period) + `left_before`. owed = {employee id: savings_owed()} decides which earlier leavers still appear (refund only)."""
    owed = owed or {}
    out, hq = [], 0
    for e in employees:
        sd, ed = _d(e.get("start_date")), (_d(e.get("end_date")) if e.get("status") == "resigned" else None)
        active = e.get("status") == "active"
        worked = (active or (ed is not None and ed >= start)) and not (sd and sd > end)
        if (e.get("pay_group") or "company") == "hq":
            hq += 1 if worked else 0
            continue
        if worked:
            out.append({**e, "first": max(start, sd) if sd else start, "last": min(end, ed) if ed else end, "left_before": False})
        elif not active:
            o = owed.get(e["id"])
            if o and o["balance"] > 0 and not o["refunded"]:
                out.append({**e, "first": None, "last": None, "left_before": True})
    out.sort(key=lambda p: (p["left_before"], BRANCHES.index(p["branch"]) if p.get("branch") in BRANCHES else 9, p.get("code") or ""))
    return out, hq


def status_of(p: dict, start: date, end: date, rate: Optional[dict]) -> dict:
    sd, ed = _d(p.get("start_date")), (_d(p.get("end_date")) if p.get("status") == "resigned" else None)
    if p.get("left_before"):
        return {"key": "left_before", "label": "ลาออกแล้ว", "detail": "รอคืนเงินสะสม" + (f" · วันสุดท้าย {dmy(ed)}" if ed else "")}
    if ed and start <= ed <= end:
        return {"key": "resigned", "label": "ลาออก", "detail": f"วันสุดท้าย {dmy(ed)}"}
    if sd and start <= sd <= end:
        return {"key": "new", "label": "พนักงานใหม่", "detail": f"เริ่ม {dmy(sd)}"}
    pe = _d(p.get("probation_end"))
    if (rate and rate.get("kind") == "probation") or (pe and pe >= start):
        return {"key": "probation", "label": "ทดลองงาน", "detail": f"ถึง {dmy(pe)}" if pe else ""}
    return {"key": "working", "label": "ทำงาน", "detail": f"จะลาออก วันสุดท้าย {dmy(ed)}" if ed else ""}


# --------------------------------------------------------------------------------------------- route drivers' days (pure)
def route_calc(rows: list[dict], first: date, last: date) -> dict:
    """One route driver's days from `first` to `last` (route_driver_days rows of his own): the days he drove (a row with a figure, not
    ticked สำรองขับ), the days the spare drove (a day off for him -- owner, 17 Nov 2026), and the dates with no row at all (not typed
    yet). Money and ซอง are of his own days only."""
    by = {str(r["day"])[:10]: r for r in rows}
    own, spare, missing = [], [], []
    total = lod = 0.0
    zong = 0
    d = first
    while d <= last:
        r = by.get(d.isoformat())
        if r is None or not (r.get("spare") or r.get("qty") or r.get("money") is not None):
            missing.append(d)
        elif r.get("spare"):
            spare.append(d)
        else:
            own.append(d)
            total += _f(r.get("money"))
            lod += _f((r.get("money_parts") or {}).get("lod"))
            zong += (r.get("qty") or {}).get("zong") or 0
        d += timedelta(days=1)
    return {"own": own, "spare": spare, "missing": missing, "money": round(total, 2), "lod": round(lod, 2), "zong": zong}


# --------------------------------------------------------------------------------------------- loans (pure)
def loan_kind(loan: dict, prev_card: float = 0) -> str:
    """What to call the loan in the note, when the data says so: HQ's ทำบัตร / passport column of last month, an accident."""
    if prev_card > 0 and loan.get("kind") != "accident":
        return "ทำบัตร / passport"
    if loan.get("kind") == "accident" or "อุบัติเหตุ" in str(loan.get("reason") or ""):
        return "อุบัติเหตุ"
    return ""


def loan_dues(loans: list[dict], entries: list[dict], month: str) -> dict[str, list[dict]]:
    """employee id -> what each of their loans asks of this pay month: {state ok|carry|miss, amount, balance, overdue, loan}.
    The same reading as loans.month_deductions (the เงินกู้ page's "หักเดือนนี้"), with the columns named and nothing written:
    ok = this month's instalment is recorded ("หักแล้ว"); carry = due on the schedule, not recorded; miss = a loan with no fixed
    instalment (ผ่อนไม่ตายตัว) that still has a balance."""
    by_loan: dict = {}
    for e in entries:
        by_loan.setdefault(e["loan_id"], []).append(e)
    look = date(int(month[:4]), int(month[5:7]), 28)
    out: dict = {}
    for loan in loans:
        if loan.get("status") not in ("active", "closed"):
            continue
        ents = by_loan.get(loan["id"], [])
        s = loan_rules.summarize(loan, ents, look)
        recorded = loan_rules.baht(sum(loan_rules.cents(e["amount"]) for e in ents if e["kind"] == "repay" and str(e.get("pay_month") or "")[:7] == month))
        row = next((r for r in s["schedule"] if r["month"] == month), None)
        due = loan_rules.baht(loan_rules.cents(row["amount"]) - loan_rules.cents(row["paid"])) if row else 0.0
        overdue = loan_rules.baht(sum(loan_rules.cents(r["amount"]) - loan_rules.cents(r["paid"]) for r in s["schedule"] if r["month"] < month and r["state"] != "paid"))
        if loan.get("status") == "closed" or s["balance"] <= 0:
            state, amount = ("ok", recorded) if recorded > 0 else (None, 0.0)
        elif loan.get("repay_mode") == "flexible":
            state, amount = ("ok", recorded) if recorded > 0 else ("miss", None)
        elif due > 0 or overdue > 0:
            state, amount = "carry", due
        else:
            state, amount = ("ok", recorded) if recorded > 0 else (None, 0.0)
        if state:
            out.setdefault(loan["employee_id"], []).append({"state": state, "amount": amount, "balance": s["balance"], "overdue": overdue, "loan": loan})
    return out


def loan_cell(items: list[dict], prev_card: float = 0) -> dict:
    if not items:
        return NA
    worst = "miss" if any(i["state"] == "miss" for i in items) else "carry" if any(i["state"] == "carry" for i in items) else "ok"
    notes = []
    for i in items:
        kind = loan_kind(i["loan"], prev_card)
        if i["state"] == "miss":
            text = f"ต้องใส่ยอด (ผ่อนไม่ตายตัว) คงเหลือ {money(i['balance'])}"
        elif i["state"] == "carry":                           # this month's instalment and anything older are two facts: never one sum
            text = " · ".join(x for x in (f"{money(i['amount'])} ตามตาราง" if i["amount"] > 0 else "งวดนี้ไม่มีตามตาราง",
                                          f"ค้างจากเดือนก่อน {money(i['overdue'])}" if i["overdue"] > 0 else "") if x) + ' — ยังไม่ได้กด "หักแล้ว"'
        else:
            text = f"{money(i['amount'])} หักแล้ว"
        notes.append((f"{kind}: " if kind else "") + text)
    # the figure of the cell is what this month asks; a loan that asks nothing this month but is behind shows what it is behind by
    known = [i["amount"] if i["amount"] > 0 or i["state"] != "carry" else i["overdue"] for i in items if i["amount"] is not None]
    total = round(sum(known), 2) if known else None
    return cell(worst, total, money(total) if total is not None else "", " · ".join(notes))


# --------------------------------------------------------------------------------------------- fuel (pure)
def trucks_by_driver(drivers: list[dict], vehicles: list[dict]) -> dict[tuple, dict]:
    """(branch, driver's name) -> the truck he is the main driver of: the fleet list, matched by full name within the driver's own
    branch, as ยอดขายรายวัน does (driver_days.drivers; both branches have a #6)."""
    names = {d["id"]: _norm(d.get("name")) for d in drivers or []}
    out: dict = {}
    for v in vehicles or []:
        name = names.get(v.get("main_driver_id"))
        key = (v.get("branch"), name)
        if name and v.get("active") is not False and (key not in out or v.get("usage") == "รถสาย"):
            out[key] = v
    return out


def truck_of(p: dict, ctx: dict) -> Optional[dict]:
    return ctx["trucks"].get((p.get("branch") or BRANCHES[0], _norm(p.get("full_name"))))


def card_fuel_view(entries: Optional[list[dict]]) -> dict:
    """Card fuel booked for a month: {by_vehicle: {vehicle id: baht}, stations: {station: {trucks, total}}, lack, estimated}.
    lack = the statements still absent, by name: "บางจาก" / "PT" when nothing of the station is booked, "บางจาก ห้วยบง" when only the
    other one of บางจาก's two station files is in (a truck that fills there would otherwise read as a false 0). One of the older
    combined entries ("fuel:bangchak:<month>:<plate>") covers both stations. estimated = stations with an estimate among their
    entries: a figure to go on, not a statement. trucks = different trucks, however many entries each has."""
    by_vehicle: dict = {}
    stations: dict = {}
    seen: dict = {}                                           # station -> the trucks (vehicle id, else the plate in the key) with an entry
    subs: set = set()                                         # บางจาก's station files that are in
    whole = False                                             # ... or one of the older entries for both stations
    estimated: list = []
    for e in entries or []:
        parts = str(e.get("source_id") or "").split(":")
        station = parts[1] if len(parts) > 1 else "?"
        amount = round(sum(_f(x.get("credit")) for x in e.get("lines") or []), 2)
        vid = next((x.get("vehicle_id") for x in e.get("lines") or [] if x.get("vehicle_id")), None)
        s = stations.setdefault(station, {"trucks": 0, "total": 0.0})
        seen.setdefault(station, set()).add(vid or parts[-1])
        s["trucks"] = len(seen[station])
        s["total"] = round(s["total"] + amount, 2)
        if parts[-1].startswith(ESTIMATE):
            if station not in estimated:
                estimated.append(station)
        elif station == "bangchak":
            if len(parts) >= 5 and parts[3] in BANGCHAK_STATIONS:
                subs.add(parts[3])
            else:
                whole = True
        if vid:
            by_vehicle[vid] = round(by_vehicle.get(vid, 0.0) + amount, 2)
    lack = []
    for key, name in STATIONS.items():
        if key not in stations:
            lack.append(name)
        elif key == "bangchak" and not whole and subs:        # only estimates: nothing to name, the cell says "ประมาณ"
            lack += [f"{name} {st}" for st in BANGCHAK_STATIONS if st not in subs]
    return {"by_vehicle": by_vehicle, "stations": stations, "lack": lack, "estimated": [STATIONS.get(k, k) for k in estimated]}


def cash_fuel_view(lines: Optional[list[dict]]) -> dict:
    """Cash fuel of a month by half: {halves: {1: {state ok|pending|none, total}, 2: ...}, by_vehicle}. Only what someone has
    confirmed against the photo counts (a confirmed page, or a line typed in); a page still to check makes its half "pending"."""
    halves = {h: {"state": "none", "total": 0.0, "pending": 0} for h in HALVES}
    by_vehicle: dict = {}
    for ln in lines or []:
        h = halves.get(ln.get("batch"))
        if h is None:
            continue
        confirmed = ln.get("page_id") is None or bool((ln.get("page") or {}).get("confirmed"))
        if not confirmed:
            h["pending"] += 1
            continue
        h["state"] = "ok"
        if ln.get("counted") and ln.get("dup_of") is None:
            h["total"] = round(h["total"] + _f(ln.get("amount")), 2)
            if ln.get("vehicle_id"):
                by_vehicle[ln["vehicle_id"]] = round(by_vehicle.get(ln["vehicle_id"], 0.0) + _f(ln.get("amount")), 2)
    for h in halves.values():
        if h["pending"]:
            h["state"] = "pending"
    return {"halves": halves, "by_vehicle": by_vehicle}


def half_labels(start: date, end: date) -> dict[int, str]:
    """The two halves of a pay month by their dates: {1: "26/10-10/11", 2: "11/11-25/11"}."""
    return {1: f"{dm(start)}-{dm(end.replace(day=10))}", 2: f"{dm(end.replace(day=11))}-{dm(end)}"}


def cash_fuel_words(view: dict, labels: dict[int, str]) -> str:
    said = {"ok": "{} มีแล้ว", "pending": "{} รอตรวจ", "none": "{} ยังไม่มี"}
    return "น้ำมันเงินสด: " + " · ".join(said[view["halves"][h]["state"]].format(labels[h]) for h in HALVES)


# --------------------------------------------------------------------------------------------- the cells of one person (pure)
def rate_spans(rates: list[dict], first: date, last: date) -> list[tuple]:
    """The daily rate across a person's days of the period, in order: [(baht or None, from, to)] -- one span, unless a rate starts
    inside (SB-PD011: 350 to 17/11/2026 on his route, 400 from 18/11/2026 at หน้าโรง)."""
    cuts = [first] + sorted({d for r in rates or [] if (d := _d(r.get("effective_from"))) and first < d <= last})
    out: list = []
    for i, a in enumerate(cuts):
        r = rate_on(rates or [], a)
        v = _f(r["daily_rate"]) if r else None
        b = cuts[i + 1] - timedelta(days=1) if i + 1 < len(cuts) else last
        if out and out[-1][0] == v:
            out[-1] = (v, out[-1][1], b)
        else:
            out.append((v, a, b))
    return out


def rate_cell(rates: list[dict], first: date, last: date, rule: Optional[dict]) -> tuple[dict, Optional[dict], list]:
    """(the cell, the rate in force on the last day, rate_spans). Two rates inside the period are both known, each for its own dates:
    that is ok (owner, 27 Nov 2026), not a stand-in. So is the second rate of production staff who also ride a truck (390 / 344, both
    confirmed): what is not known there is how many days go with each, and that gap sits on the days cell."""
    spans = rate_spans(rates or [], first, last)
    r = rate_on(rates or [], last)
    if not r:
        return cell("miss", note="ยังไม่มีอัตราค่าแรง"), None, spans
    value, state, notes = _f(r["daily_rate"]), "ok", []
    text = money(value)
    if len(spans) > 1:
        text = " / ".join(money(v) if v is not None else "–" for v, _a, _b in spans)
        for i, (v, a, b) in enumerate(spans):
            if v is None:                                    # days before his first rate: nothing to pay them at
                state = "carry"
                notes.append(f"ยังไม่มีอัตรา ถึง {dmy(b)}")
            else:
                notes.append(f"{money(v)} ถึง {dmy(b)}" if i == 0 else f"{money(v)} ตั้งแต่ {dmy(a)}")
    rate2 = (rule or {}).get("rate2")
    if rate2:
        text = f"{text} / {money(rate2)}"
        notes.append(f"2 อัตรา: วันออกรถอย่างเดียว {money(rate2)}")
    if r.get("review"):
        state = "carry"
        notes.append("อัตรารอตรวจ" + (f": {r['review_note']}" if r.get("review_note") else ""))
    return cell(state, value, text, " · ".join(notes)), r, spans


# --------------------------------------------------------------------------------------------- the rule in words (pure)
def wage_words(spans: list[tuple], rate2=None) -> str:
    """ค่าแรง of one person: "วัน × 350" -- each rate with its dates when it changed inside the period, and the second rate of someone
    who also rides a truck."""
    known = [(i, v, a, b) for i, (v, a, b) in enumerate(spans) if v is not None]
    if not known:
        return "วัน × (ยังไม่มีอัตรา)"
    if len(spans) == 1:
        v = known[0][1]
        return f"วันทำงานในโรงงาน × {num(v)} + วันออกรถอย่างเดียว × {num(rate2)}" if rate2 else f"วัน × {num(v)}"
    words = " + ".join(f"วัน × {num(v)} (ถึง {dmy(b)})" if i == 0 else f"วัน × {num(v)} (ตั้งแต่ {dmy(a)})" for i, v, a, b in known)
    return words + (f" + วันออกรถอย่างเดียว × {num(rate2)}" if rate2 else "")


# how a number reads in a rule, and what stands in its place when there is not one number: (for a whole group, for one person)
RULE_VALUES = {"pct": ("{}%", "% ของแต่ละคน", "(ยังไม่มี %)"), "bag": ("{}", "อัตราของแต่ละคน", "(ยังไม่มีอัตราต่อซอง)"),
               "ot": ("{}", "อัตรา OT ของแต่ละคน", "(ยังไม่มีอัตรา OT)")}


def formula_words(group: str, wage: str, vals: dict, bonus: str = "", person: bool = False) -> str:
    """A group's rule in the workbook's words with the numbers filled in: "ค่าแรง = วัน × 120 · ค่าคอม = (ยอดขาย − ค่าน้ำมันรถ) × 5% · ...".
    vals = {pct, bag, ot}: a number, or None when there is not one. person = one person's own rule: a part he has no number for is
    named as missing -- or left out where the rule says "ถ้ามี" (OPTIONAL)."""
    parts = [f"ค่าแรง = {wage}"]
    for label, text in GROUP_RULES.get(group, []):
        lack = [k for k in RULE_VALUES if "{" + k + "}" in text and vals.get(k) is None]
        if lack and group in OPTIONAL:
            if not person:
                parts.append(OPTIONAL[group])
            continue
        said = text.format(**{k: (shape.format(num(vals[k])) if vals.get(k) is not None else one if person else many)
                              for k, (shape, many, one) in RULE_VALUES.items()})
        parts.append(f"{label} = {said}" if label else said)
    if bonus:
        parts.append(bonus)
    return " · ".join(parts)


def person_formula(group: str, rule: Optional[dict], spans: list[tuple], days: int, ot: bool = False) -> str:
    """One person's rule with HIS rate, %, baht per ซอง, and whether he can get เบี้ยขยัน. "" for someone with no pay group.
    ot = his OT hours are an input (แก่งคอย, paid OT on HQ's sheet) though his group's rule names none: the workbook's "OT ถ้ามี"."""
    if not group or not rule:
        return ""
    vals = {k: (_f(v) if v not in (None, "") else None) for k, v in (("pct", rule.get("commission_pct")), ("bag", rule.get("bag_rate")), ("ot", rule.get("ot_rate")))}
    said = [f"เบี้ยขยัน {money(BONUS_BAHT)} ถ้ามาครบ {days} วัน" if rule.get("bonus") else "ไม่มีเบี้ยขยัน"]
    if ot and not any(label == "OT" for label, _text in GROUP_RULES.get(group, [])):
        said.insert(0, "OT = " + OT_RULE.format(ot=num(vals["ot"]) if vals["ot"] is not None else RULE_VALUES["ot"][2]))
    return formula_words(group, wage_words(spans, _f(rule.get("rate2")) or None), vals, " · ".join(said), person=True)


def build_rules(rows: list[dict], ctx: dict) -> dict:
    """The rules card: the general rules, one line each, and one line per pay group -- its rule with the group's real numbers where
    everyone in it has the same one, how many people of this period are in it (and how many of those are set aside), and whether
    the group gets เบี้ยขยัน. A group nobody is in still shows, with the workbook's numbers."""
    start, end = ctx["start"], ctx["end"]
    general = [t.format(start=dmy(start), end=dmy(end), days=(end - start).days + 1, bonus=money(BONUS_BAHT), bag_price=BAG_PRICE) for t in RULES_GENERAL]
    if HOLD_BRANCHES:
        general.append(RULE_HOLD.format(hold=hold_words()))
    worked = [p for p in rows if p["status"]["key"] != "left_before"]
    groups = []
    for g in GROUP_ORDER + sorted({p["group"] for p in worked if p["group"] and p["group"] not in GROUP_ORDER}):
        mine = [p for p in worked if p["group"] == g]
        # the numbers come from the people the rule still describes: not from someone left in a route group after his route ended
        # (SB-PD011 in 2026-12: 400 at หน้าโรง, and DRV-E's line must go on saying 350)
        like = [p for p in mine if p["id"] not in ctx["off_rule"]]
        rules = [ctx["rules"].get(p["id"]) or {} for p in like]

        def one(key: str, values: list):
            got = {round(_f(v), 2) for v in values if v not in (None, "")}
            return GROUP_DEFAULTS.get(g, {}).get(key) if not like else got.pop() if len(got) == 1 else None
        rate = one("rate", [ctx["base_rate"].get(p["id"]) for p in like])
        vals = {"pct": one("pct", [r.get("commission_pct") for r in rules]), "bag": one("bag", [r.get("bag_rate") for r in rules]),
                "ot": one("ot", [r.get("ot_rate") for r in rules])}
        bonus = any(r.get("bonus") for r in rules) if like else g in DRIVER_GROUPS
        tail = [f"เบี้ยขยัน {money(BONUS_BAHT)}"] if bonus else []
        # the workbook's "OT ถ้ามี": a group whose rule names no OT, with people whose OT hours are asked (แก่งคอย's DAY people)
        with_ot = sorted({p["branch"] for p in mine if p["cells"]["ot"]["state"] != "na"}, key=lambda b: BRANCHES.index(b) if b in BRANCHES else 9)
        if with_ot and not any(label == "OT" for label, _text in GROUP_RULES.get(g, [])):
            tail.insert(0, f"({', '.join(with_ot)}: + OT ถ้ามี)")
        row = {"group": g, "label": GROUP_LABELS.get(g, g), "people": len(mine), "bonus": bonus,
               "formula": formula_words(g, f"วัน × {num(rate)}" if rate is not None else "วัน × อัตราของแต่ละคน", vals, " · ".join(tail))}
        if any(p["hold"] for p in mine):
            row["hold"] = sum(1 for p in mine if p["hold"])
        groups.append(row)
    return {"general": general, "groups": groups, "source": RULES_SOURCE}


def build_person(p: dict, ctx: dict) -> dict:
    """One row of the per-person table: every column decided. ctx = what build() prepared (the period, the sources by person)."""
    start, end = ctx["start"], ctx["end"]
    rule = ctx["rules"].get(p["id"])
    group = (rule or {}).get("pay_group") or ""
    base = {"id": p["id"], "code": p.get("code") or "", "name": _norm(p.get("full_name")), "nickname": p.get("nickname") or "", "branch": p.get("branch") or "",
            "position": p.get("position") or "", "group": group, "group_label": GROUP_LABELS.get(group, group),
            "hold": not p["left_before"] and held(p.get("branch"))}        # set aside with his branch: listed, in no count
    cells = {k: NA for k in KEYS}
    owed = ctx["owed"].get(p["id"])
    if p["left_before"]:
        cells["refund"] = refund_cell(owed)
        return _finish({**base, "status": status_of(p, start, end, None), "rate": None, "rate2": None, "formula": ""}, cells)
    first, last = p["first"], p["last"]
    own, prev = sheet_rows(p, ctx)                           # HQ's sheet of this very month (HQ already paid it), else the latest one before
    ref = own or prev
    parts, deds = (ref or {}).get("parts") or {}, (ref or {}).get("deductions") or {}

    # ---- group, rate
    cells["group"] = cell("ok", text=group, note=GROUP_LABELS.get(group, "")) if rule else cell("miss", note="ยังไม่ได้ตั้งกลุ่มคิดเงิน")
    if ctx["rates"] is None:
        cells["rate"], rate, spans = cell("miss", note="โหลดอัตราค่าแรงไม่สำเร็จ"), None, []
    else:
        cells["rate"], rate, spans = rate_cell(ctx["rates"].get(p["id"], []), first, last, rule)
    rate2 = _f((rule or {}).get("rate2")) or None

    # ---- days, sales, bags
    scans = {d for d in ctx["scans"].get(p["id"], ()) if first <= d <= last}
    until = _d((rule or {}).get("route_until")) if group in ROUTE_GROUPS else None
    route = on_route(p, rule)                                # False as well for a route that ended before his first day of the period
    bags = bool((rule or {}).get("bag_rate"))
    rc = ctx["route_own"].get(p["id"])
    # the rate that goes with the group's rule: of the route, for someone whose route ended inside the period (build_rules)
    ctx["base_rate"][p["id"]] = (spans[0][0] if route and until and until < last else spans[-1][0]) if spans else None
    if group in ROUTE_GROUPS and not route:                  # left in a route group after his route ended: the group's rule is not his any more
        ctx["off_rule"].add(p["id"])
    net = route and group in NET_OF_FUEL_GROUPS              # commission on sales after the truck's fuel: what is typed is before it
    if route and group in FUEL_GROUPS:                       # the truck's fuel: off the sales of A, B, C; off ทวาย's ซอง
        cells["fuel"] = fuel_cell(p, ctx)
    if first > last:                                         # a start date after the last day: nothing can be counted
        cells["days"] = cell("miss", note="วันเริ่มงาน / วันสุดท้ายในระบบไม่ถูกต้อง")
        if route:
            cells["sales"] = cells["days"]
    elif route and rc is None:
        cells["days"] = cells["sales"] = cell("miss", note="โหลดยอดขายรายวันไม่สำเร็จ")
        if bags:
            cells["bags"] = cells["days"]
    elif route and rc["early"]:                              # the first day of the period: nothing is expected before tomorrow
        cells["days"] = cell("carry", 0, "0 วัน", EARLY)
        cells["sales"] = cell("carry", 0, "0", EARLY)
        if bags:
            cells["bags"] = cell("carry", 0, "0 ซอง", EARLY)
    elif route:
        state, notes = "ok", []
        if rc["missing"]:
            state = "miss"
            notes.append("ยังไม่ได้ลง " + runs(rc["missing"]))
        if rc["spare"]:
            notes.append(f"หยุด {len(rc['spare'])} วัน (สำรองขับ)")
        worked = len(rc["own"])
        if until and until < last:                           # the route ended: other work after that -- his scans stand in for those days
            after = [until + timedelta(days=i) for i in range(1, (last - until).days + 1)]
            seen = sorted(d for d in scans if d > until)
            span = f"{dm(after[0])}-{dm(after[-1])}" if len(after) > 1 else dm(after[0])
            if seen:
                state = "carry" if state == "ok" else state
                notes += [f"ขับรถสาย {worked} วัน", f"{span} ทำงานอื่น: มีสแกน {len(seen)} วัน ยังไม่ได้สรุป"]
                worked += len(seen)
            else:
                state = "miss"
                notes.append(f"{span} ทำงานอื่น ยังไม่มีข้อมูลวันทำงาน")
        cells["days"] = cell(state, worked, f"{worked} วัน", " · ".join(notes))
        gap = f"ยังขาด {len(rc['missing'])} วัน" if rc["missing"] else ""
        split = p["id"] in ctx["split"]
        sold = rc["lod"] if split else rc["money"]
        # the truck's fuel is of the whole period, his sales and ซอง of his own days only: whose fuel a สำรองขับ day is has not been
        # settled (build_decisions asks), so the figure after fuel and the fuel itself say that those days are in it
        spare = f"รวมค่าน้ำมันของวันสำรองขับ {len(rc['spare'])} วัน" if rc["spare"] else ""
        if spare and cells["fuel"]["state"] != "na" and truck_of(p, ctx):
            cells["fuel"] = {**cells["fuel"], "note": f"{cells['fuel']['note']} · {spare}"}
        fuel = cells["fuel"]
        after = f" · หลังหัก {money(sold - fuel['value'])}" + (f" ({spare})" if spare else "") if fuel["state"] == "ok" and not rc["missing"] else ""
        before = "ก่อนหักค่าน้ำมัน" + after if net else ""
        cells["sales"] = cell("miss" if rc["missing"] else "ok", sold, money(sold), " · ".join(x for x in ("เฉพาะยอดหลอด" if split else "", before, gap) if x))
        if bags:
            cells["bags"] = cell("miss" if rc["missing"] else "ok", rc["zong"], f"{rc['zong']:,} ซอง", gap)
    else:
        driver = ctx["route_own"].get((rule or {}).get("rides_with"))
        if scans:
            cells["days"] = cell("carry", len(scans), f"{len(scans)} วัน", f"มีสแกน {len(scans)} วัน ยังไม่ได้สรุปเป็นวันทำงาน")
        elif driver and driver["early"]:
            cells["days"] = cell("carry", 0, "0 วัน", f"ตามคนขับ {driver['who']} · {EARLY}")
        elif driver:                                         # a เด็กรถ: his driver's own days stand in for his
            n = sum(1 for d in driver["own"] if first <= d <= last)
            gaps = [d for d in driver["missing"] if first <= d <= last]
            cells["days"] = (cell("miss", n, f"{n} วัน", f"ตามคนขับ {driver['who']}: {n} วัน · คนขับยังไม่ได้ลง {runs(gaps)}") if gaps
                             else cell("carry", n, f"{n} วัน", f"ตามคนขับ {driver['who']}: {n} วัน (ยังไม่ยืนยัน)"))
        else:
            cells["days"] = cell("miss", note="ยังไม่มีสแกนนิ้ว / ใบสรุปวันหยุด")
        if until:                                            # still in a route group, but the route ended before this period
            c = cells["days"]
            cells["days"] = {**c, "note": f"ขับรถสายถึง {dmy(until)} · {c['note']}"}          # route_until is the last day he drove
        elif _f((ref or {}).get("sales")) > 0:               # paid a % of sales that IceBox has nowhere to take from (แก่งคอย)
            cells["sales"] = cell("miss", note=f"ยังไม่มีแหล่งข้อมูล (ใบ HQ {my(ref['month'])} คิดจากยอดขาย {money(ref['sales'])})")
        if bags and not until:
            cells["bags"] = cell("miss", note="ยังไม่มีแหล่งข้อมูล")
    if rate2:
        c = cells["days"]
        cells["days"] = {**c, "note": " · ".join(x for x in (c["note"], f"ต้องแยกวันออกรถ ({money(rate2)})") if x)}

    # ---- OT hours, trip money (Kaeng Khoi)
    kk = p.get("branch") == "แก่งคอย"
    if group == "KK-PROD" or (kk and _f(parts.get("ot")) > 0):
        cells["ot"] = cell("carry", note="มีสแกน ยังไม่ได้สรุป OT") if scans else cell("miss", note="ยังไม่มีชั่วโมง OT (ใบสรุปสิ้นเดือนแก่งคอย)")
    if (group == "KK-DRV" or (kk and _f(parts.get("commission")) > 0)) and cells["sales"]["state"] == "na":      # a % of sales is asked as ยอดขาย, never both
        trips = ctx["trips"]
        if not trips:
            cells["trip"] = cell("miss", note="ยังไม่มีตารางเที่ยวงวดนี้" if trips is not None else "โหลดตารางเที่ยวไม่สำเร็จ")
        else:
            mine = [t for t in trips if (t.get("employee_code") or "").upper() == (p.get("code") or "").upper()]
            total = round(sum(_f(t.get("computed_total") if t.get("computed_total") is not None else t.get("written_total")) for t in mine), 2)
            cells["trip"] = (cell("carry", total, money(total), "ตามตารางเที่ยว (ยังไม่ยืนยัน)") if mine
                             else cell("carry", note="มีตารางเที่ยวงวดนี้ แต่ยังไม่มีของคนนี้"))

    # ---- deductions: HQ's own sheet of the month when there is one, else the latest sheet before it as the stand-in
    fines = ctx["fines"].get(p["id"], [])
    if own:
        said = f"ตามใบ HQ {my(own['month'])} (HQ คิดงวดนี้แล้ว)"
        for key in ("savings", "advance", "common", "other", "sso"):
            if _f(deds.get(key)) > 0:
                cells[key] = cell("ok", _f(deds[key]), money(deds[key]), said)
        if not fines and _f(deds.get("shortage")) > 0:
            cells["penalty"] = cell("ok", _f(deds["shortage"]), money(deds["shortage"]), "เงินขาด " + said)
    elif ctx["prev_failed"]:
        cells["advance"] = cells["savings"] = cells["sso"] = cell("miss", note="โหลดใบสรุปเงินเดือน HQ ไม่สำเร็จ")
    elif prev is None:
        cells["advance"] = cells["savings"] = cells["sso"] = cell("miss", note=(
            f"ไม่มีชื่อในใบ HQ {my(ctx['sheet'])} — ยังไม่รู้ว่าต้องหักหรือไม่" if ctx["sheet"] else f"ไม่มีใบ HQ ของ {LOOK_BACK} เดือนล่าสุดให้ใช้เป็นค่าแทน"))
        if ctx["sheet"]:                                     # สะสม follows HQ's latest sheet: not on it = nothing to take (a new hire), said in amber
            cells["savings"] = cell("carry", 0, "0", f"ไม่มีชื่อในใบ HQ {my(ctx['sheet'])}: ไม่หักสะสม — ถ้าจะเริ่มหักต้องแจ้ง")
    else:
        latest = prev["month"] == ctx["sheet"]
        said = f"ตามใบ HQ {my(prev['month'])}" + ("" if latest else f" (ไม่มีชื่อในใบ {my(ctx['sheet'])})")
        listed = bool((ctx["advance_in"] or {}).get(p.get("branch")))                 # the branch's mid-month list is booked (as pages, not per person)
        wait = ("มีใบเบิกแล้ว แต่ระบบยังอ่านรายคนไม่ได้ · " if listed else "") + WAIT_ADVANCE
        if _f(deds.get("advance")) > 0:
            cells["advance"] = cell("carry", _f(deds["advance"]), money(deds["advance"]), f"{said} · {wait}")
        elif not listed:                                     # who draws changes every month: "none last month" is not "none this month"
            cells["advance"] = cell("carry", 0, "0", f"ใบ HQ {my(prev['month'])} ไม่ได้เบิก · {wait}")
        if _f(deds.get("savings")) > 0:                      # owner, 27 Nov 2026: สะสม follows HQ's latest sheet -- confirmed, no stand-in
            cells["savings"] = (cell("ok", _f(deds["savings"]), money(deds["savings"]), f"{said} (เจ้าของยืนยัน)") if latest
                                else cell("carry", _f(deds["savings"]), money(deds["savings"]), said))
        if _f(deds.get("sso")) > 0:
            cells["sso"] = cell("carry", _f(deds["sso"]), money(deds["sso"]), f"{said} · {WAIT_SSO}")
        if _f(deds.get("common")) > 0:
            cells["common"] = cell("miss", note=f"ใบ HQ {my(prev['month'])} หัก {money(deds['common'])} บาท · เปลี่ยนทุกเดือน ต้องใส่ใหม่")
    card = _f(((prev or own or {}).get("deductions") or {}).get("card"))
    cells["loan"] = loan_cell(ctx["loans"].get(p["id"], []), card)                    # ทำบัตร of the last sheet IS this loan: never both
    if fines:
        total = round(sum(f["amount"] for f in fines), 2)
        cells["penalty"] = cell("ok", total, money(total), "ใบเตือน " + ", ".join(f"{dmy(f['day'])} {money(f['amount'])}" for f in fines))
    status = status_of(p, start, end, rate)
    if status["key"] == "resigned" and owed and owed["balance"] > 0 and not owed["refunded"]:
        cells["refund"] = refund_cell(owed, short=True)
    return _finish({**base, "status": status, "rate": cells["rate"]["value"], "rate2": rate2,
                    "formula": person_formula(group, rule, spans, (end - start).days + 1, ot=cells["ot"]["state"] != "na")}, cells)


EARLY = "งวดเพิ่งเริ่ม ยังไม่ถึงวันที่ต้องลง"
WAIT_SSO = "รอเจ้าของตรวจ"                          # owner, 27 Nov 2026: "ปกส. I will check and tell you"
WAIT_ADVANCE = "รอรายชื่อเบิกจากเจ้าของ"            # owner, 27 Nov 2026: "เบิกล่วงหน้า I have to send to you"


def on_route(p: dict, rule: Optional[dict]) -> bool:
    """A route driver in this period: in a สระบุรี route group, or a พนักงานขับรถสาย of the other branch (ยอดขายรายวัน takes route drivers of
    both branches since 26 Nov 2026) -- and the route had not ended before his first day of it."""
    until = _d((rule or {}).get("route_until"))
    route = (rule or {}).get("pay_group") in ROUTE_GROUPS or p.get("position") == ROUTE_POSITION
    return route and not (until and p.get("first") and until < p["first"])


def sheet_rows(p: dict, ctx: dict) -> tuple[Optional[dict], Optional[dict]]:
    """(HQ's pay row of this very month, the latest one before it) for a person: by id, else by code."""
    code = (p.get("code") or "").upper()
    return ctx["own"].get(p["id"]) or ctx["own_code"].get(code), ctx["prev"].get(p["id"]) or ctx["prev_code"].get(code)


def refund_cell(owed: dict, short: bool = False) -> dict:
    """Savings to pay back to someone who left. short = a person leaving in this very period (the balance is last month's, as printed)."""
    said = f" · HQ หมายเหตุ: {owed['said']}" if owed.get("said") else ""
    if owed.get("unsure"):                                   # a later sheet printed no balance: only the last printed figure, to be checked
        return cell("carry", owed["balance"], money(owed["balance"]),
                    f"ใบ HQ ล่าสุด ({my(owed['last'])}) ไม่ได้พิมพ์ยอดสะสม — ยอดล่าสุดที่พิมพ์ {money(owed['balance'])} บาท ({my(owed['month'])}) ตรวจว่าคืนแล้วหรือยัง{said}")
    words = f"สะสม {money(owed['balance'])} บาท ยังไม่ได้คืน"
    return cell("miss", owed["balance"], money(owed["balance"]), words + ("" if short else f" (ยอดตามใบ HQ {my(owed['month'])})") + said)


def fuel_cell(p: dict, ctx: dict) -> dict:
    """The fuel of a route driver's truck over the pay month: the card statements + cash fuel. ok only when all of it is in -- every
    card station's statement booked (บางจาก: both of its station files) and both cash halves confirmed -- and then 0 baht is a real
    answer. A card figure that is an estimate makes it a stand-in. Otherwise missing: the note names what is absent and what is
    already known; the cell itself carries no figure, so a part of the fuel never reads as the truck's fuel."""
    truck = truck_of(p, ctx)
    if not truck:
        return cell("miss", note="ไม่พบรถของคนขับ (ทะเบียนรถไม่มีชื่อคนนี้เป็นคนขับหลัก)")
    card, cash, labels = ctx["card"], ctx["cash"], half_labels(ctx["start"], ctx["end"])
    lack, have = [], []                                      # what is absent; what is already known of this truck (shown only when it is money)
    on_card = in_cash = 0.0
    if card is None:
        lack.append("โหลดข้อมูลบัตรน้ำมันไม่สำเร็จ")
    else:
        on_card = card["by_vehicle"].get(truck["id"], 0.0)
        if not card["stations"]:
            lack.append("ยังไม่มีใบแจ้งหนี้บัตรน้ำมัน")
        elif card["lack"]:                                   # "บางจาก ห้วยบง": one of บางจาก's two station files is in, the other is not
            lack.append("ยังไม่มีใบแจ้งหนี้บัตรน้ำมัน " + ", ".join(card["lack"]))
        if on_card > 0:
            have.append(f"บัตร {money(on_card)}")
    if cash is None:
        lack.append("โหลดน้ำมันเงินสดไม่สำเร็จ")
    else:
        states = {h: cash["halves"][h]["state"] for h in HALVES}
        lack += [f"น้ำมันเงินสด {labels[h]} " + ("ยังไม่ได้อัปโหลด" if states[h] == "none" else "อัปโหลดแล้ว รอตรวจ") for h in HALVES if states[h] != "ok"]
        in_cash = cash["by_vehicle"].get(truck["id"], 0.0)
        if in_cash > 0:
            have.append(f"เงินสด {money(in_cash)}")
    if not lack:
        total = round(on_card + in_cash, 2)
        rough = (card or {}).get("estimated") or []
        return cell("carry" if rough else "ok", total, money(total), f"รถ {truck_label(truck)} · บัตร {money(on_card)} + เงินสด {money(in_cash)}"
                    + (f" · บัตร {', '.join(rough)} เป็นยอดประมาณ ยังไม่ใช่ใบแจ้งหนี้" if rough else ""))
    return cell("miss", note=" · ".join([f"รถ {truck_label(truck)}"] + lack + (["มีแล้ว: " + " + ".join(have)] if have else [])))


def _finish(person: dict, cells: dict) -> dict:
    states = {c["state"] for c in cells.values()}
    person["state"] = "miss" if "miss" in states else "carry" if "carry" in states else "ok"
    person["missing"] = [TITLES[k] for k in KEYS if cells[k]["state"] == "miss"]
    person["cells"] = cells
    return person


def _who(person: dict) -> str:
    """who() for a row of the answer (its name is under "name")."""
    return who({**person, "full_name": person["name"]})


# --------------------------------------------------------------------------------------------- roll-ups (pure)
def kind_notes(ctx: dict) -> dict[str, str]:
    """What is simply not known yet about a kind of input, whatever its cells say: a grey dash must never read as an all-clear
    while the papers that would carry the answer are not in."""
    if ctx["hq_done"]:
        return {}
    out = {"nobonus": "ยังไม่รู้ว่างดของใคร", "other": "ยังไม่รู้ว่ามีของใคร"}
    papers, advance = ctx["papers_in"], ctx["advance_in"]
    branches = [b for b in BRANCHES if not held(b)]          # a branch that is set aside is not waited for
    if papers is None or not all(papers.get(b) for b in branches):
        out["penalty"] = "ยังไม่ได้อัปโหลดเอกสารสิ้นเดือนครบ จึงยังไม่รู้ว่ามีใบเตือน / เงินขาดหรือไม่"
        out["other"] = "ยังไม่ได้อัปโหลดเอกสารสิ้นเดือนครบ จึงยังไม่รู้ว่ามีของใคร"
    if advance is None or not all(advance.get(b) for b in branches):
        out["common"] = "ยังไม่ได้อัปโหลดใบเบิกกลางเดือนครบ จึงยังไม่รู้ว่างวดนี้หักของใคร"
    else:
        out["common"] = "มีใบเบิกแล้ว แต่ยังไม่มีที่ลงยอดส่วนกลางรายคน"
    return out


def build_kinds(people: list[dict], notes: Optional[dict] = None, sources: Optional[dict] = None) -> list[dict]:
    """One line per input: how many people need it, how many have it, and who does not: the worst first, a line that says something
    of its own before the lines many people share (those are folded into one -- "107 คน"). People of a branch that is set aside are in
    none of it: only their number is given ("hold", when there are any). sources = a kind's source line, when the data says more."""
    notes, sources = notes or {}, sources or {}
    out = []
    for key, title, _short, source, link, label, later in KINDS:
        source = sources.get(key, source)
        aside = sum(1 for p in people if p.get("hold") and p["cells"][key]["state"] != "na")
        got = [(p, p["cells"][key]) for p in people if not p.get("hold") and p["cells"][key]["state"] != "na"]
        n = {s: sum(1 for _, c in got if c["state"] == s) for s in ("ok", "carry", "miss")}
        same: dict = {}
        for p, c in got:
            if c["state"] != "ok":
                same.setdefault((c["state"], " · ".join(x for x in (c["text"], c["note"]) if x)), []).append(p)
        lines = []                                            # (miss first, shared lines last and the biggest first, code), the line, people in it
        for (state, detail), ps in same.items():
            tone = "bad" if state == "miss" else "warn"
            if len(ps) >= FOLD:
                lines.append(((state != "miss", 1, -len(ps), ""), {"who": f"{len(ps)} คน", "detail": detail, "state": tone, "count": len(ps)}, len(ps)))
            else:
                lines += [((state != "miss", 0, 0, p["code"]), {"who": _who(p), "detail": detail, "state": tone}, 1) for p in ps]
        lines.sort(key=lambda x: x[0])
        note = notes.get(key, "")
        state = "na" if not got else "bad" if n["miss"] else "warn" if n["carry"] else "ok"
        out.append({"key": key, "title": title, "state": "warn" if note and state in ("na", "ok") else state,
                    "need": len(got), **n, "source": source, "rows": [x[1] for x in lines[:MAX_ROWS]], "more": sum(x[2] for x in lines[MAX_ROWS:]),
                    "link": link, "link_label": label, "note": note, "later": later, **({"hold": aside} if aside else {})})
    return out


def _file(key: str, title: str, state: str, summary: str, link: Optional[str] = None, label: Optional[str] = None) -> dict:
    return {"key": key, "title": title, "state": state, "summary": summary, "link": link, "link_label": label}


def _by_branch(found: dict, what: str) -> tuple[str, str]:
    """(state, words) for something each branch hands in: found = {branch: number of pages}. A branch that is set aside is not
    waited for: its papers are named when they are in, and their absence is no gap."""
    have = [b for b in BRANCHES if found.get(b)]
    lack = [b for b in BRANCHES if not found.get(b) and not held(b)]
    aside = [b for b in BRANCHES if not found.get(b) and held(b)]
    if not have:
        return "bad", "ยังไม่ได้อัปโหลด"
    words = " · ".join(f"{b} {found[b]} {what}" for b in have) + (" · ยังไม่มี: " + ", ".join(lack) if lack else "")
    return ("warn" if lack else "ok"), words + "".join(f" · {b}พักไว้ก่อน" for b in aside)


def unknown_codes(uploads: Optional[list[dict]], employees: list[dict], start: date, end: date) -> list[str]:
    """The codes the fingerprint files of this period carry that are not in the staff list -- their scans are counted for nobody.
    From attendance_uploads.unknown_codes ("SB-FR112", or "SB-KK005 name, ..."): the latest upload of each file, and only codes that
    are still not an employee's."""
    latest: dict = {}
    for u in sorted(uploads or [], key=lambda u: str(u.get("created_at") or "")):
        a, b = _d(u.get("period_start")), _d(u.get("period_end"))
        if a and b and a <= end and b >= start:
            latest[u.get("filename") or u.get("id")] = u
    known = {(e.get("code") or "").upper() for e in employees}
    out: dict = {}
    for u in latest.values():
        for token in str(u.get("unknown_codes") or "").split(","):
            token = _norm(token)
            code = token.split(" ")[0].upper()
            if code and code not in known:
                out.setdefault(code, token)
    return [out[c] for c in sorted(out)]


def doc_pages(docs: Optional[list[dict]], what: str) -> Optional[dict]:
    """{branch: pages booked} of the mid-month advance lists ("advance") or the end-of-month papers ("endmonth"); None = not read."""
    if docs is None:
        return None
    return {b: sum(1 for d in docs if d.get("branch") == b and (d.get("kind") == "advance" if what == "advance" else d.get("feed") == "endmonth")) for b in BRANCHES}


def build_files(people: list[dict], src: dict, ctx: dict, month: str) -> list[dict]:
    """The things to upload or press, in the order to do them."""
    start, end, upto = ctx["start"], ctx["end"], ctx["upto"]
    out = []
    aside = hold_words()

    def set_aside(f: dict) -> dict:
        """A line only about a branch that is set aside: information, never a to-do (a read that failed stays what it is)."""
        return f if f["summary"] == "โหลดไม่สำเร็จ" else {**f, "state": "info", "summary": f"{aside} · {f['summary']}"}
    # ---- route sales
    if ctx["route"] is None:
        out.append(_file("route_sales", "ยอดขายรายวัน รถสาย", "bad", "โหลดไม่สำเร็จ", "#driver-days", "ยอดขายรายวัน"))
    elif upto < start:
        out.append(_file("route_sales", "ยอดขายรายวัน รถสาย", "info", EARLY, "#driver-days", "ยอดขายรายวัน"))
    else:
        own = [r for pid, r in ctx["route_own"].items() if pid not in ctx["hold_ids"]]         # a driver of a branch set aside is not chased
        gaps = sorted(((r["who"], r["missing"]) for r in own if r["missing"]), key=lambda x: (-len(x[1]), x[0]))
        days = sum(len(m) for _, m in gaps)
        if gaps and ctx["hq_done"]:                           # a month HQ worked out from its own papers: nothing to type any more
            state, words = "info", f"งวดนี้ HQ คิดแล้ว — ในระบบไม่มียอดขายรายวัน {days:,} วัน ของ {len(gaps)} คน"
        elif gaps:                                            # who and which dates, one person a line: the only people to chase today
            state, words = "bad", "\n".join([f"ยังไม่ได้ลง {days:,} วัน ของ {len(gaps)} คน"] + [f"{w}: {runs(m)}" for w, m in gaps[:6]]
                                            + ([f"และอีก {len(gaps) - 6} คน"] if len(gaps) > 6 else []))
        else:
            state, words = "ok", f"ลงครบ {len(own)} คน ถึง {dmy(upto)}"
        out.append(_file("route_sales", "ยอดขายรายวัน รถสาย", state, words, "#driver-days", "ยอดขายรายวัน"))
    # ---- fingerprint files, by the code prefix of the people who have scans
    code = {e["id"]: (e.get("code") or "").upper() for e in src["employees"]}
    for key, prefix, title, branch in SCAN_GROUPS:
        if src.get("punches") is None:
            out.append(_file(key, title, "bad", "โหลดไม่สำเร็จ", "#attendance", "เวลาทำงาน"))
            continue
        mine = [pid for pid in ctx["scans"] if code.get(pid, "").startswith(prefix)]
        if not mine:
            f = _file(key, title, "bad", "ยังไม่ได้อัปโหลดงวดนี้", "#attendance", "เวลาทำงาน")
        else:
            days = sorted(set().union(*(ctx["scans"][pid] for pid in mine)))
            count = sum(ctx["scan_count"][pid] for pid in mine)
            short = (days[0] - start).days > 2 or (upto - days[-1]).days > 1
            f = _file(key, title, "warn" if short else "ok",
                      (f"มีสแกนแค่ {dmy(days[0])} ถึง {dmy(days[-1])} ({len(mine)} คน)" if short else f"{len(mine)} คน {count:,} สแกน ({dm(days[0])} ถึง {dm(days[-1])})"),
                      "#attendance", "เวลาทำงาน")
        strangers = [c for c in ctx["unknown"] if c.upper().startswith(prefix)]          # in the file, not in the staff list: counted for nobody
        if strangers:
            f = {**f, "state": "bad" if f["state"] == "bad" else "warn", "link": "#employees", "link_label": "รายชื่อพนักงาน",
                 "summary": f["summary"] + "\nมีรหัสที่ไม่อยู่ในรายชื่อพนักงาน: " + ", ".join(strangers[:6])
                            + (f" และอีก {len(strangers) - 6} รหัส" if len(strangers) > 6 else "") + " — เพิ่มพนักงานก่อน ไม่งั้นไม่ถูกนับ"}
        out.append(set_aside(f) if held(branch) else f)
    # ---- the branch papers (acc_docs: the script's "2026-11-saraburi", the drop box's "docs:2026-11:<branch>:mid|end")
    docs, inbox = src.get("docs"), src.get("inbox") or []
    waiting = sum(1 for f in inbox if f.get("kind") == "scan" and f.get("status") in WAITING)       # a file kept on purpose ("stored") is not waiting
    broken = sum(1 for f in inbox if f.get("kind") == "scan" and f.get("status") == "failed")
    wait_words = (f" · มี {waiting} ไฟล์สแกนในกล่องรับไฟล์ ยังไม่ได้ลง" if waiting else "") + (f" · อ่านไม่สำเร็จ {broken} ไฟล์" if broken else "")
    if docs is None:
        out.append(_file("advance_list", "ใบเบิกกลางเดือน", "bad", "โหลดไม่สำเร็จ", "#accounts-in", "ลงข้อมูล / แนบไฟล์"))
        out.append(_file("month_papers", "เอกสารสิ้นเดือน (ใบสรุปสิ้นเดือน)", "bad", "โหลดไม่สำเร็จ", "#accounts-in", "ลงข้อมูล / แนบไฟล์"))
    else:
        state, words = _by_branch(doc_pages(docs, "advance"), "หน้า")
        out.append(_file("advance_list", "ใบเบิกกลางเดือน", state, words + (wait_words if state != "ok" else ""), "#accounts-in", "ลงข้อมูล / แนบไฟล์"))
        state, words = _by_branch(doc_pages(docs, "endmonth"), "หน้า")
        out.append(_file("month_papers", "เอกสารสิ้นเดือน (ใบสรุปสิ้นเดือน)", state, words + (wait_words if state != "ok" else ""), "#accounts-in", "ลงข้อมูล / แนบไฟล์"))
    # ---- fuel: both lines now carry the pay of every route driver paid after fuel (A, B, C) as well as ทวาย's ซอง
    trucks = fuel_trucks(people)
    used = f"\nใช้คิดเงินของรถสาย {trucks} คัน (กลุ่ม A, B, C หักจากยอดขาย · ทวาย หักจากซอง)" if trucks else ""
    card = ctx["card"]
    if card is None:
        out.append(_file("card_fuel", "ใบแจ้งหนี้บัตรน้ำมัน", "bad", "โหลดไม่สำเร็จ", "#accounts-in", "ลงข้อมูล / แนบไฟล์"))
    elif not card["stations"]:
        out.append(_file("card_fuel", "ใบแจ้งหนี้บัตรน้ำมัน", "bad", "ยังไม่มีใบแจ้งหนี้บัตรน้ำมันงวดนี้ (บางจาก / PT)" + used, "#accounts-in", "ลงข้อมูล / แนบไฟล์"))
    else:
        words = " · ".join(f"{STATIONS.get(k, k)} {v['trucks']} คัน {money(v['total'])} บาท" for k, v in sorted(card["stations"].items()))
        lack, rough = card["lack"], card["estimated"]        # a station (or one of บางจาก's two) not in yet; a figure that is only an estimate
        out.append(_file("card_fuel", "ใบแจ้งหนี้บัตรน้ำมัน", "warn" if lack or rough else "ok",
                         words + (" · ยังไม่มี: " + ", ".join(lack) if lack else "") + (" · ยอดประมาณ: " + ", ".join(rough) if rough else "") + used,
                         "#accounts-in", "ลงข้อมูล / แนบไฟล์"))
    cash = ctx["cash"]
    if cash is None:
        out.append(_file("cash_fuel", "น้ำมันเงินสด", "bad", "โหลดไม่สำเร็จ", "#cash-fuel", "น้ำมันเงินสด"))
    else:
        label = half_labels(start, end)                       # a half is named by its dates: "งวด" on this page is the whole pay month
        states = {h: cash["halves"][h]["state"] for h in HALVES}
        done = [h for h in HALVES if states[h] == "ok"]
        words = " · ".join((f"{label[h]} ยืนยันแล้ว {money(cash['halves'][h]['total'])} บาท" if states[h] == "ok"
                            else f"{label[h]} อัปโหลดแล้ว รอตรวจ" if states[h] == "pending" else f"{label[h]} ยังไม่ได้อัปโหลด") for h in HALVES)
        out.append(_file("cash_fuel", "น้ำมันเงินสด", "ok" if len(done) == 2 else "warn" if done else "bad",
                         (words if any(s != "none" for s in states.values()) else f"ยังไม่ได้อัปโหลดทั้ง 2 ช่วง ({label[1]}, {label[2]})") + used,
                         "#cash-fuel", "น้ำมันเงินสด"))
    # ---- Kaeng Khoi trip tables (no upload of their own yet: someone has to load them)
    trips = ctx["trips"]
    f = _file("kk_trips", "ตารางเที่ยว แก่งคอย", "ok" if trips else "bad",
              f"{len(trips)} ตาราง" if trips else "ยังไม่มีตารางเที่ยวงวดนี้ — ยังอัปโหลดเองไม่ได้ ต้องแจ้งให้ลงให้" if trips is not None else "โหลดไม่สำเร็จ")
    out.append(set_aside(f) if held("แก่งคอย") else f)
    # ---- loans: "หักแล้ว"
    if src.get("loans") is None or src.get("loan_entries") is None:
        out.append(_file("loans_mark", "เงินกู้: กด \"หักแล้ว\" ของเดือนนี้", "bad", "โหลดไม่สำเร็จ", "#loans", "เงินกู้"))
    else:
        on_list = {p["id"] for p in people if not p["hold"]}                  # the same people the loan kind counts
        items = [i for pid, rows in ctx["loans"].items() if pid in on_list for i in rows]
        held_ids = {p["id"] for p in people if p["hold"]}                     # a branch set aside: said, never part of the to-do
        kept = [i for pid, rows in ctx["loans"].items() if pid in held_ids for i in rows if i["state"] != "ok"]
        kept_baht = sum(i["amount"] or 0 for i in kept)
        due = [i for i in items if i["state"] == "carry" and i["amount"] > 0]
        late = [i for i in items if i["state"] == "carry" and i["overdue"] > 0]
        done = [i for i in items if i["state"] == "ok"]
        loose = [i for i in items if i["state"] == "miss"]
        tail = ((f" · ค้างจากเดือนก่อน {len(late)} รายการ {money(sum(i['overdue'] for i in late))} บาท" if late else "")
                + (f" · ผ่อนไม่ตายตัว {len(loose)} รายการ ต้องใส่ยอดเอง" if loose else "")
                + (f" · {aside} {len(kept)} รายการ" + (f" {money(kept_baht)} บาท" if kept_baht else "") if kept else ""))
        if due:
            state, words = "warn", f"ยังไม่ได้กด {len(due)} รายการ {money(sum(i['amount'] for i in due))} บาท" + (f" (กดแล้ว {len(done)})" if done else "")
        elif done:
            state, words = "ok", f"กดแล้ว {len(done)} รายการ {money(sum(i['amount'] for i in done))} บาท"
        else:
            state, words = "info", "เดือนนี้ไม่มีงวดที่ต้องหัก"
        out.append(_file("loans_mark", "เงินกู้: กด \"หักแล้ว\" ของเดือนนี้", "warn" if (loose or late) and state != "warn" else state, words + tail, "#loans", "เงินกู้"))
    # ---- HQ's sheets: the one before (the stand-in for เบิก / สะสม / ปกส.) and this month's own (to compare against)
    n = ctx["hq_done"]
    if not n:
        title, want, sheet = "ใบสรุปเงินเดือน HQ เดือนก่อน (สะสมหักตามใบนี้ · ค่าแทนของ เบิก / ปกส.)", add_month(month, -1), ctx["sheet"]
        if ctx["prev_failed"]:
            out.append(_file("prev_sheet", title, "bad", "โหลดไม่สำเร็จ"))
        elif not sheet:
            out.append(_file("prev_sheet", title, "warn", f"ไม่มีใบ HQ ของ {LOOK_BACK} เดือนล่าสุด — สะสม / เบิก / ปกส. ไม่มียอดให้ใช้"))
        elif sheet == want:
            out.append(_file("prev_sheet", title, "ok", f"ใช้ใบ {my(sheet)} ({ctx['sheet_people']} คน)"))
        else:                                                 # HQ's sheet of last month may never come: IceBox is taking the calculation over
            out.append(_file("prev_sheet", title, "info", f"ยังไม่มีใบ {my(want)} — ใช้ใบ {my(sheet)} แทน ({ctx['sheet_people']} คน)"))
    tail_rows = src.get("pay_tail")
    dropped = sum(1 for f in inbox if f.get("kind") == "payroll" and f.get("status") in WAITING)
    out.append(_file("hq_sheet", "ใบสรุปเงินเดือนจาก HQ (ไว้เทียบ)", "ok" if n else "info",
                     f"มีแล้ว {n} คน" if n else ("โหลดไม่สำเร็จ" if tail_rows is None else
                                                 "ยังไม่มี (ใช้เทียบผลเท่านั้น)" + (f" · มี {dropped} ไฟล์ในกล่องรับไฟล์ ยังไม่ได้ลง" if dropped else ""))))
    # ---- a person with no start date can never show as "พนักงานใหม่": he is expected for the whole period
    paid = {p["id"] for p in people if p["status"]["key"] != "left_before" and not p["hold"]}
    blank = sum(1 for e in src["employees"] if e["id"] in paid and not e.get("start_date"))
    if blank:
        out.append(_file("start_dates", "วันเริ่มงานของพนักงาน", "info",
                         f"{blank:,} คนยังไม่มีวันเริ่มงานในระบบ — ถ้ามีคนเข้าใหม่ระหว่างงวด ต้องใส่วันเริ่มงาน ไม่งั้นจะถูกนับเต็มงวด", "#employees", "รายชื่อพนักงาน"))
    return out


def fuel_trucks(people: list[dict]) -> int:
    """How many trucks' fuel the calculation needs: the route drivers of groups A, B, C and ทวาย (not those of a branch set aside)."""
    return sum(1 for p in people if not p["hold"] and p["cells"]["fuel"]["state"] != "na")


def build_decisions(people: list[dict], ctx: dict) -> list[dict]:
    """What still stands between the data and numbers that can be trusted (at most 10), built from the data: the rules the owner has
    yet to settle, then what he said he would send ("wait": true). Settled on 27 Nov 2026 and no longer asked: who gets เบี้ยขยัน
    (drivers only), แก่งคอย's OT (the branch is set aside: nothing about its people is asked), sales before or after fuel (after)."""
    out = []
    worked = [p for p in people if p["status"]["key"] != "left_before" and not p["hold"]]
    rules, rates = ctx["rules"], ctx["rates"] or {}
    no_rate = [p for p in worked if p["cells"]["rate"]["state"] == "miss" and p["status"]["key"] != "resigned"]
    if no_rate:
        out.append({"title": "ยังไม่มีอัตราค่าแรง: " + ", ".join(_who(p) for p in no_rate[:6]) + (f" และอีก {len(no_rate) - 6} คน" if len(no_rate) > 6 else ""),
                    "detail": "ต้องใส่อัตรา/วันในหน้า รายชื่อพนักงาน ก่อน จึงจะคิดค่าแรงได้"})
    ended = [(p, _d(rules[p["id"]].get("route_until"))) for p in worked if p["group"] in ROUTE_GROUPS and (rules.get(p["id"]) or {}).get("route_until")]
    # a route that ended inside the period: asked only while no rate starts after it (SB-PD011 has 400 from 18/11/2026: settled)
    during = [(p, d) for p, d in ended if d and ctx["start"] <= d < ctx["end"]
              and not any(d < (_d(r.get("effective_from")) or d) <= ctx["end"] for r in rates.get(p["id"], []))]
    before = [(p, d) for p, d in ended if d and d < ctx["start"]]
    if during:
        out.append({"title": "รถสายที่หยุดวิ่งระหว่างงวด: วันหลังจากนั้นคิดอัตราไหน",
                    "detail": " · ".join(f"{_who(p)} ขับรถสายถึง {dmy(d)} หลังจากนั้นทำงานอื่น ยังไม่มีอัตราของช่วงนั้น" for p, d in during[:4])})
    if before:
        out.append({"title": "เลิกขับรถสายแล้ว แต่ยังอยู่กลุ่มรถสาย: ต้องตั้งกลุ่มและอัตราใหม่",
                    "detail": " · ".join(f"{_who(p)} ขับรถสายถึง {dmy(d)} — งวดนี้ไม่มีวันขับรถสาย แต่กติกายังเป็นกลุ่ม {p['group']}" for p, d in before[:4])})
    for p in worked:                                          # HQ pays him a % of sales, but the rules carry no % and IceBox has no sales of his
        ref = next((r for r in sheet_rows(p, ctx) if r), None)
        if len(out) < 6 and p["cells"]["sales"]["state"] == "miss" and p["group"] not in ROUTE_GROUPS and ref and not _f((rules.get(p["id"]) or {}).get("commission_pct")):
            paid, sold = _f((ref.get("parts") or {}).get("commission")), _f(ref.get("sales"))
            pct = f" (ประมาณ {paid / sold * 100:.1f}%)".replace(".0%", "%") if paid > 0 and sold > 0 else ""
            out.append({"title": f"ค่าคอมจากยอดขาย: {_who(p)} ยังไม่มี % ในกติกา",
                        "detail": f"ใบ HQ {my(ref['month'])} จ่ายค่าคอม {money(paid)} จากยอดขาย {money(sold)}{pct} ไม่ใช่ค่าเที่ยว — "
                                  "กติกาของคนนี้ยังไม่มี % และยังไม่มีที่ลงยอดขายของคนนี้ในระบบ"})
    # the workbook writes "ยอดขาย × 3.5%" for C, the page works on the หลอด part (his ซอง are paid apart, and typed apart): to confirm
    tubes = [p for p in worked if p["group"] == "DRV-C" and p["id"] in ctx["split"] and p["cells"]["sales"]["state"] != "na"]
    if tubes:
        r = rules.get(tubes[0]["id"]) or {}
        pct, bag = (num(r[k]) if r.get(k) not in (None, "") else "" for k in ("commission_pct", "bag_rate"))
        out.append({"title": f"{', '.join(_who(p) for p in tubes[:3])}: ค่าคอม {pct}% คิดจากยอดหลอดอย่างเดียว หรือรวมยอดซอง",
                    "detail": f"แบบคำนวณเขียนว่า \"ยอดขาย × {pct}%\" แต่ค่าซองจ่ายแยกแล้ว {bag} บาท/ซอง — หน้านี้ใช้ยอดหลอดอย่างเดียว (ตามที่ HQ คิด) "
                              "ถ้าต้องรวมยอดซองด้วยต้องแจ้ง"})
    # the truck's fuel is of the whole period; a day the spare drove is not the driver's own day
    spare = [(p, len(ctx["route_own"][p["id"]]["spare"])) for p in worked
             if p["group"] in FUEL_GROUPS and p["cells"]["fuel"]["state"] != "na" and (ctx["route_own"].get(p["id"]) or {}).get("spare")]
    if spare:
        out.append({"title": "วันสำรองขับ: ค่าน้ำมันรถของวันนั้นหักจากใคร",
                    "detail": ", ".join(f"{_who(p)} {n} วัน" for p, n in spare[:6]) + (f" และอีก {len(spare) - 6} คน" if len(spare) > 6 else "")
                              + " — ยอดขายนับเฉพาะวันที่ขับเอง แต่ค่าน้ำมันรถเป็นของทั้งงวด ตอนนี้ค่าน้ำมันของวันสำรองขับยังรวมอยู่ในของคนขับประจำ"})
    two = [p for p in worked if p["rate2"]]
    if two:
        out.append({"title": "พนักงานผลิตที่ออกรถด้วย: ต้องรู้จำนวนวันออกรถอย่างเดียว",
                    "detail": ", ".join(f"{_who(p)} ({money(p['rate'])} / {money(p['rate2'])})" for p in two[:6])
                              + " — วันออกรถอย่างเดียวคิดอัตราที่สอง ตอนนี้ยังไม่มีที่บันทึกว่าเป็นวันไหน"})
    out.append({"title": "สแกนนิ้วครั้งเดียวในวัน: นับเป็น 1 วันเต็มหรือไม่",
                "detail": "HQ นับเป็น 1 วันเต็ม (ไม่เคยจ่ายครึ่งวัน) — ต้องตกลงก่อนใช้สแกนนิ้วสรุปเป็นวันทำงาน"})
    # what the owner said he would bring: shown while a stand-in is still in its place
    waiting = []
    for key, title, what in (("sso", "ปกส.: เจ้าของกำลังตรวจ", "รอเจ้าของตรวจและแจ้งยอดของงวดนี้"),
                             ("advance", "เบิกล่วงหน้า: รอรายชื่อจากเจ้าของ", "รอรายชื่อคนเบิกของงวดนี้จากเจ้าของ")):
        standing = sum(1 for p in worked if p["cells"][key]["state"] == "carry")
        if standing:
            waiting.append({"title": title, "detail": f"ตอนนี้ใช้ยอดจากใบ HQ ล่าสุดแทน {standing:,} คน — {what}"})
    return [{**d, "wait": False} for d in out[:10 - len(waiting)]] + [{**d, "wait": True} for d in waiting]


# --------------------------------------------------------------------------------------------- the whole answer (pure)
def frame(month: str, today: date) -> dict:
    start, end = period_bounds(month)
    return {"month": month, "start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days + 1, "closed": today > end, "months": months(today)}


def build(src: dict, month: str, today: date) -> dict:
    """The checklist of one pay month from plain rows. src = the tables' rows by name (see _read); a value of None = that source
    could not be read (it is named in src["failed"], and whoever needs it shows as missing)."""
    start, end = period_bounds(month)
    upto = min(end, today - timedelta(days=1))               # a running month is only expected up to yesterday (before `start` on its first day)
    employees = src["employees"]
    by_id = {e["id"]: e for e in employees}
    by_code = {(e.get("code") or "").upper(): e["id"] for e in employees}

    tail: dict = {}                                                   # HQ's pay rows per person (by id, else by code)
    for r in src.get("pay_tail") or []:
        pid = r.get("employee_id") or by_code.get((r.get("code") or "").upper())
        if pid:
            tail.setdefault(pid, []).append(r)
    owed = {pid: o for pid, rows in tail.items() if (o := savings_owed(rows))}
    people, hq_paid = roster(employees, start, end, owed)

    rates = None
    if src.get("rates") is not None:
        rates = {}
        for r in src["rates"]:
            rates.setdefault(r["employee_id"], []).append(r)
    route = None
    if src.get("route") is not None:
        route = {}
        for r in src["route"]:
            route.setdefault(r["employee_id"], []).append(r)
    scans: dict = {}                                                  # person -> the dates of the period with a scan
    scan_count: dict = {}
    for r in src.get("punches") or []:
        d = _d(r.get("punched_at"))
        if d and start <= d <= end:
            scans.setdefault(r["employee_id"], set()).add(d)
            scan_count[r["employee_id"]] = scan_count.get(r["employee_id"], 0) + 1
    fines: dict = {}
    for doc in src.get("penalties") or []:
        day = _d(doc.get("doc_date"))
        if day and start <= day <= end and _f(doc.get("penalty")) > 0 and doc.get("status") not in ("draft", "cancelled"):
            fines.setdefault(doc["employee_id"], []).append({"day": day, "amount": _f(doc["penalty"])})
    # HQ's sheets: this month's own rows (HQ already worked the month out), and per person the LATEST row before it -- the stand-in.
    # HQ's sheet of the month before may never come (IceBox is taking the calculation over), so an older one is used and named.
    sheets = src.get("pay_prev")
    own, own_code, prev, prev_code = {}, {}, {}, {}
    for r in sorted(sheets or [], key=lambda r: str(r.get("month") or "")):          # oldest first: a later row replaces an earlier one
        m, code = str(r.get("month") or ""), (r.get("code") or "").upper()
        if not m or m > month:
            continue
        by_person, by_its_code = (own, own_code) if m == month else (prev, prev_code)
        if r.get("employee_id"):
            by_person[r["employee_id"]] = r
        by_its_code[code] = r
    before = [str(r["month"]) for r in sheets or [] if r.get("month") and str(r["month"]) < month]
    sheet = max(before) if before else None
    rules = {r["employee_id"]: r for r in src.get("rules") or []}
    ctx = {"start": start, "end": end, "upto": upto, "rules": rules, "rates": rates, "route": route, "scans": scans, "scan_count": scan_count, "fines": fines, "owed": owed,
           "split": {r["employee_id"] for r in src.get("split") or [] if r.get("money_split")},
           "own": own, "own_code": own_code, "prev": prev, "prev_code": prev_code, "prev_failed": sheets is None,
           "sheet": sheet, "sheet_people": before.count(sheet) if sheet else 0,
           "hq_done": max(len(own_code), sum(1 for r in src.get("pay_tail") or [] if r.get("month") == month)),
           "advance_in": doc_pages(src.get("docs"), "advance"), "papers_in": doc_pages(src.get("docs"), "endmonth"),
           "loans": loan_dues(src["loans"], src["loan_entries"], month) if src.get("loans") is not None and src.get("loan_entries") is not None else {},
           "trips": src.get("trips"), "card": card_fuel_view(src["card_fuel"]) if src.get("card_fuel") is not None else None,
           "cash": cash_fuel_view(src["cash_fuel"]) if src.get("cash_fuel") is not None else None,
           "trucks": trucks_by_driver(src.get("drivers") or [], src.get("vehicles") or []),
           "unknown": unknown_codes(src.get("uploads"), employees, start, end), "base_rate": {}, "off_rule": set(),
           "hold_ids": {p["id"] for p in people if not p["left_before"] and held(p.get("branch"))}}
    # every route driver's own days first: a helper's days follow his driver's. early = nothing is expected of him yet.
    ctx["route_own"] = {}
    for p in people:
        rule = rules.get(p["id"]) or {}
        if route is not None and not p["left_before"] and on_route(p, rule) and p["first"] <= p["last"]:
            stop = min(x for x in (p["last"], upto, _d(rule.get("route_until"))) if x)
            ctx["route_own"][p["id"]] = {**route_calc(route.get(p["id"], []), p["first"], stop), "who": who(by_id[p["id"]]), "early": stop < p["first"]}

    rows = [build_person(p, ctx) for p in people]
    paid = [p for p in rows if p["status"]["key"] != "left_before"]              # everyone being paid this month
    worked = [p for p in paid if not p["hold"]]                                  # the headline counts them, less a branch that is set aside
    n = {s: sum(1 for p in worked if p["state"] == s) for s in ("ok", "carry", "miss")}
    trucks = fuel_trucks(rows)
    sources = {"fuel": f"{TITLES_SOURCE['fuel']} ({trucks} คัน)"} if trucks else {}
    # savings owed to someone who left are money to pay back: counted whatever is set aside, and the line says whose are among them
    for b in HOLD_BRANCHES:
        gone = sum(1 for p in rows if p["branch"] == b and p["cells"]["refund"]["state"] != "na" and not p["hold"])
        if gone:
            sources["refund"] = sources.get("refund", TITLES_SOURCE["refund"]) + f" · รวมคนที่ลาออกของ{b} {gone} คน (เงินที่ต้องคืน ไม่ได้พักไว้)"
    return {"ready": True, **frame(month, today), "hq_done": ctx["hq_done"], "hold_branches": list(HOLD_BRANCHES),
            "summary": {"people": len(worked), **n, "hq_paid": hq_paid, "worked": len(worked), "left_before": len(rows) - len(paid), "hold": len(paid) - len(worked)},
            "rules": build_rules(rows, ctx),
            "files": build_files(rows, src, ctx, month), "kinds": build_kinds(rows, kind_notes(ctx), sources),
            "columns": [{"key": k[0], "label": k[1], "short": k[2]} for k in KINDS], "people": rows,
            "decisions": build_decisions(rows, ctx), "failed": list(src.get("failed") or [])}


# --------------------------------------------------------------------------------------------- the database (reads only)
_probe = {"at": 0.0, "ok": False}


def has_v93() -> bool:
    """Is employee_pay_rules there? Only "no such table" answers False (kept 30 s): any other failure is raised, so a hiccup of the
    database reads as "could not load", never as an instruction to run a schema file that is already in."""
    from app import supabase
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("employee_pay_rules", {"select": "employee_id", "limit": "1"})
        ok = True
    except supabase.SupabaseError as exc:
        if not any(x in str(exc) for x in NO_TABLE):
            raise
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _read(month: str) -> dict:
    """Every source of one pay month, read side by side. Only the people list is required; any other source that fails (an error
    answer or a timeout) is None and named in "failed"."""
    from app import supabase
    start, end = period_bounds(month)
    s, e = start.isoformat(), end.isoformat()
    cut = [start + timedelta(days=i) for i in range(0, (end - start).days + 1, 8)] + [end + timedelta(days=1)]     # scans in four slices
    back = add_month(month, -LOOK_BACK)                      # HQ's sheets: the last months whole, everything older only for the savings

    def scans(a: date, b: date) -> Callable[[], list]:
        return lambda: supabase.select_all("attendance_punches", {"select": SELECTS["attendance_punches"], "punched_at": f"gte.{a.isoformat()}T00:00:00",
                                                                  "and": f"(punched_at.lt.{b.isoformat()}T00:00:00)", "order": "punched_at.asc,id.asc"})

    parts: list[tuple[str, str, Callable[[], list]]] = [
        ("employees", "รายชื่อพนักงาน", lambda: supabase.select_all("employees", {"select": SELECTS["employees"], "order": "code.asc,id.asc"})),
        ("rules", "กลุ่มคิดเงิน", lambda: supabase.select_all("employee_pay_rules", {"select": SELECTS["employee_pay_rules"], "order": "employee_id.asc"})),
        ("rates", "อัตราค่าแรง", lambda: supabase.select_all("employee_rates", {"select": SELECTS["employee_rates"], "order": "effective_from.desc,id.asc"})),
        ("pay_prev", "ใบสรุปเงินเดือน HQ", lambda: supabase.select_all("employee_pay_months", {"select": SELECTS["pay_prev"], "month": f"gte.{back}",
                                                                                            "order": "month.desc,id.asc"})),
        ("pay_old", "ยอดสะสมจากใบ HQ", lambda: supabase.select_all("employee_pay_months", {"select": SELECTS["pay_old"], "month": f"lt.{back}",
                                                                                         "order": "month.desc,id.asc"})),
        ("route", "ยอดขายรายวัน", lambda: supabase.select_all("route_driver_days", {"select": SELECTS["route_driver_days"], "day": f"gte.{s}", "and": f"(day.lte.{e})",
                                                                                   "order": "day.asc,id.asc"})),
        ("split", "ยอดขายรายวัน (แยกซอง/หลอด)", lambda: supabase.select_all("route_driver_settings", {"select": SELECTS["route_driver_settings"], "order": "employee_id.asc"})),
        *[(f"punches{i}", "สแกนนิ้ว", scans(cut[i], cut[i + 1])) for i in range(len(cut) - 1)],
        ("uploads", "ไฟล์สแกนนิ้ว", lambda: supabase.select_all("attendance_uploads", {"select": SELECTS["attendance_uploads"], "period_end": f"gte.{s}",
                                                                                      "and": f"(period_start.lte.{e})", "order": "created_at.asc,id.asc"})),
        ("loans", "เงินกู้", lambda: supabase.select_all("employee_loans", {"select": SELECTS["employee_loans"], "order": "loan_date.desc,id.asc"})),
        ("loan_entries", "เงินกู้", lambda: supabase.select_all("employee_loan_entries", {"select": SELECTS["employee_loan_entries"],
                                                                                        "order": "entry_date.asc,created_at.asc,id.asc"})),
        ("card_fuel", "บัตรน้ำมัน", lambda: supabase.select_all("acc_entries", {"select": SELECTS["acc_entries"], "source": "eq.card_fuel",
                                                                              "source_id": f"like.fuel:*:{month}:*", "order": "id.asc"})),
        ("cash_fuel", "น้ำมันเงินสด", lambda: supabase.select_all("cash_fuel_lines", {"select": SELECTS["cash_fuel_lines"], "month": f"eq.{month}", "order": "id.asc"})),
        ("vehicles", "ทะเบียนรถ", lambda: supabase.select_all("vehicles", {"select": SELECTS["vehicles"], "order": "id.asc"})),
        ("drivers", "ทะเบียนรถ", lambda: supabase.select_all("drivers", {"select": SELECTS["drivers"], "order": "id.asc"})),
        ("trips", "ตารางเที่ยว แก่งคอย", lambda: supabase.select_all("fin_trip_tables", {"select": SELECTS["fin_trip_tables"], "month": f"eq.{month}", "order": "id.asc"})),
        ("docs", "เอกสารเบิก", lambda: supabase.select_all("acc_docs", {"select": SELECTS["acc_docs"], "or": f'(batch.like."{month}-*",batch.like."docs:{month}:*")',
                                                                     "order": "batch.asc,page.asc"})),
        ("inbox", "กล่องรับไฟล์", lambda: supabase.select_all("inbox_files", {"select": SELECTS["inbox_files"], "month": f"eq.{month}", "order": "id.asc"})),
        ("penalties", "ใบเตือน", lambda: supabase.select_all("employee_documents", {"select": SELECTS["employee_documents"], "doc_type": "eq.warning", "doc_date": f"gte.{s}",
                                                                                   "and": f"(doc_date.lte.{e})", "order": "doc_date.asc,id.asc"})),
    ]

    def guarded(name: str, fn: Callable[[], list]) -> Callable[[], Optional[list]]:
        def run():
            try:
                return fn()
            except (supabase.SupabaseError, httpx.HTTPError):         # an error answer, or no answer at all (a timeout, a dropped connection)
                if name == "employees":
                    raise
                logger.exception("payroll checklist: %s", name)
                return None
        return run

    got = supabase.parallel(*[guarded(name, fn) for name, _label, fn in parts])
    src: dict = {"failed": []}
    for (name, label, _fn), rows in zip(parts, got):
        if rows is None and label not in src["failed"]:
            src["failed"].append(label)
        if name.startswith("punches"):
            src["punches"] = None if rows is None or (name != "punches0" and src.get("punches") is None) else (src.get("punches") or []) + rows
        else:
            src[name] = rows
    # every HQ row, light: for the savings of people who left, and for "is HQ's own sheet of this month in"
    recent, old = src.get("pay_prev"), src.pop("pay_old", None)
    src["pay_tail"] = None if recent is None or old is None else old + [
        {"month": r.get("month"), "code": r.get("code"), "employee_id": r.get("employee_id"), "refund": r.get("refund"), "flags": r.get("flags"),
         "savings_balance": r.get("savings_balance"), "bag": (r.get("parts") or {}).get("bag"), "other": (r.get("parts") or {}).get("other")} for r in recent]
    return src


_answers: dict = {}                               # (month, today) -> (kept until, the answer)
_locks: dict = {}
_guard = threading.Lock()


def forget() -> None:
    with _guard:
        _answers.clear()


def load(month: str, today: date, fresh: bool = False) -> dict:
    """GET /api/payroll/checklist: the checklist of one pay month ("" = the month being paid now). Kept CACHE_SECONDS per month;
    fresh=True reads again."""
    month = parse_month(month) if month else default_month(today)
    offered = months(today)
    if month > offered[0]:
        raise PayrollError("ยังไม่ถึงงวดนี้")
    if month < offered[-1]:
        raise PayrollError(f"ดูย้อนหลังได้ถึงงวด {my(offered[-1])}")
    if not has_v93():
        return {"ready": False, "need": NEEDS, **frame(month, today), "summary": {"people": 0, "ok": 0, "carry": 0, "miss": 0, "hq_paid": 0, "hold": 0},
                "hold_branches": list(HOLD_BRANCHES), "rules": {"general": [], "groups": [], "source": RULES_SOURCE}, "files": [], "kinds": [], "columns": [{"key": k[0], "label": k[1], "short": k[2]} for k in KINDS], "people": [], "decisions": [], "failed": []}
    key = (month, today.isoformat())
    seen = _answers.get(key)
    if not fresh and seen and time.time() < seen[0]:
        return seen[1]
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        hit = _answers.get(key)
        if hit and time.time() < hit[0] and (not fresh or hit is not seen):          # another request read it while this one waited
            return hit[1]
        out = build(_read(month), month, today)
        with _guard:
            if len(_answers) > 40:
                _answers.clear()
            _answers[key] = (time.time() + (RETRY_SECONDS if out["failed"] else CACHE_SECONDS), out)
        return out
