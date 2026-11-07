"""
บัญชี › เงินเดือน: the monthly payroll summaries the payroll accountant emails ("รายละเอียดเงินเดือน MM/69": สรุปเงินเดือน ช่าง+ออฟฟิศ+
ฝ่ายผลิต สระบุรี / พขร. สระบุรี / แก่งคอย). The PDFs' Thai text is garbled (font without a Unicode map) but every number is exact.

The sheet is also how petty cash gets paid back: rows with no employee, labelled with a half-month ("ค่าใช้จ่าย 11-25/9/69",
"ค่าน้ำมัน เงินสด 11-25/9/69", Saraburi before August: "คืนสำรองค่าใช้จ่าย 11-25/01/2569"). Those amounts are the end-of-month petty
cash already counted from the end-of-month papers, so they are taken OUT of gross and net here -- never wages, never paid twice.

What is read: the total row -- gross pay (รวมรับ), deductions (รวมจ่าย), net (คงเหลือ) -- and the 8 deduction columns that follow it, in
this fixed order: สะสม, เบิกล่วงหน้า, คืนเงินกู้, ส่วนกลาง, passport, ความผิด, อุปกรณ์/อื่นๆ, ปกส. The triple is found by its arithmetic
(gross - deductions = net), so a column moving does not matter; the 8 deductions must add up to the deductions total.

One entry per summary, dated the 25th of the company month (the pay month runs 26th - 25th):
  Dr wages (gross: the real cost)        5400 factory / office / mechanics (and Kaeng Khoi, one sheet) · 6200 drivers
  Cr 2400 สะสม       savings held back, paid out when someone leaves
  Cr 1500 เบิกล่วงหน้า  the mid-month advance already paid in cash (booked from the mid-month papers) -- cleared, not paid twice
  Cr 1510 คืนเงินกู้    a factory loan paid back (the loan was booked when it was lent)
  Cr 7400 ส่วนกลาง     staff-housing water / electricity charged back: the bills are already in petty cash, this lowers their cost
  Cr 7120 passport   foreign-worker paperwork the company paid by cheque, charged back
  Cr 4900 ความผิด, อุปกรณ์  fines and equipment charged to staff: other income
  Cr 7110 ปกส.       the employee's 5% social security: the company pays SSO both halves by cheque (booked 7110), so this leaves only
                    the employer's half as the cost
  Cr 1100 net        paid out (cash, and bank transfer)
parse_summary / build_entry are pure and unit-tested.
"""
import re
from datetime import date
from typing import Optional

DEDUCTIONS = [("สะสม", "2400"), ("เบิกล่วงหน้า", "1500"), ("คืนเงินกู้", "1510"), ("ส่วนกลาง", "7400"), ("passport", "7120"),
              ("ความผิด", "4900"), ("อุปกรณ์ / อื่นๆ", "4900"), ("ปกส. (ส่วนลูกจ้าง)", "7110")]
GROUPS = {"FAC": ("สระบุรี", "5400", "ช่าง / ออฟฟิศ / ฝ่ายผลิต สระบุรี"), "DRV": ("สระบุรี", "6200", "พนักงานขับรถ สระบุรี"),
          "KK": ("แก่งคอย", "5400", "พนักงานแก่งคอย")}
NUM = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


def _v(t: str) -> float:
    return 0.0 if t == "-" else float(t.replace(",", ""))


HALF = re.compile(r"1[01]\s*-\s*25\s*/\s*\d{1,2}\s*/\s*(?:25)?6\d")


def reimbursements(tokens: list[str]) -> list[float]:
    """Petty-cash pay-back rows: the first amount after each half-month label."""
    lines = [t.strip() for t in tokens if t.strip()]
    out = []
    for i, t in enumerate(lines):
        if HALF.search(t):
            amt = next((lines[j] for j in range(i + 1, min(i + 5, len(lines))) if NUM.match(lines[j]) and "," in lines[j]), None)
            if amt:
                out.append(_v(amt))
    return out


def parse_summary(tokens: list[str]) -> dict:
    """tokens: the text lines of the whole summary. -> {gross, deductions, net, parts: [8] or None, reimburse: [..]}."""
    toks = [t.strip() for t in tokens if t.strip() and (NUM.match(t.strip()) or t.strip() == "-")]
    best = None
    for i in range(len(toks) - 2):
        if "-" in toks[i:i + 3]:
            continue
        a, b, c = (_v(x) for x in toks[i:i + 3])
        if a > 0 and b > 0 and c > 0 and abs(a - b - c) < 1 and (best is None or a > best[0]):
            best = (a, b, c, i)
    if not best:
        raise ValueError("ไม่พบยอดรวมรับ / รวมจ่าย / คงเหลือ")
    gross, ded, net, i = best
    parts = None
    for start in range(i + 3, min(i + 8, len(toks) - 7)):       # the 8 deduction columns follow (an older sheet has 1-2 extra first)
        w = [_v(x) for x in toks[start:start + 8]]
        if abs(sum(w) - ded) < 1:
            parts = w
            break
    return {"gross": gross, "deductions": ded, "net": net, "parts": parts, "reimburse": reimbursements(tokens)}


def build_entry(month: str, group: str, s: dict, source_note: str = "") -> dict:
    branch, wage_acc, label = GROUPS[group]
    y, m = (int(x) for x in month.split("-"))
    back = round(sum(s.get("reimburse") or []), 2)          # petty cash paid back through the sheet: not wages
    lines = [(wage_acc, s["gross"] - back, 0.0, None)]
    if s["parts"]:
        for (name, acc), amt in zip(DEDUCTIONS, s["parts"]):
            if amt:
                lines.append((acc, 0.0, amt, name))
    else:                                                       # breakdown not readable: the whole deduction waits in 1500, flagged
        lines.append(("1500", 0.0, s["deductions"], "รายการหักแยกไม่ได้ — ตรวจ"))
    lines.append(("1100", 0.0, s["net"] - back, "จ่ายสุทธิ (เงินสด + โอน)"))
    return {"entry_date": date(y, m, 25).isoformat(), "branch": branch, "source": "payroll", "source_id": f"payroll:{month}:{group}",
            "party": f"เงินเดือน {label}", "doc_type": "payroll", "doc_no": None,
            "description": f"เงินเดือน {label} {m:02d}/{y + 543} · ค่าจ้าง {s['gross'] - back:,.0f} · หัก {s['deductions']:,.0f} · สุทธิ {s['net'] - back:,.0f}"
                           + (f" · ไม่รวมเงินคืนสำรองจ่าย {back:,.0f} (นับจากเอกสารสิ้นเดือนแล้ว)" if back else "")
                           + (" · แยกรายการหักไม่ได้" if not s["parts"] else "") + (f" · {source_note}" if source_note else ""),
            "lines": [{"account": a, "debit": round(d, 2), "credit": round(c, 2), "branch": branch, "vehicle_id": None, "machine_id": None,
                       "memo": memo} for a, d, c, memo in lines]}


def group_of(file_name: str) -> Optional[str]:
    if "สรุป" not in file_name or "รับสด" in file_name or not file_name.lower().endswith(".pdf"):
        return None
    return "KK" if "แก่งคอย" in file_name else ("DRV" if "พขร" in file_name else "FAC")
