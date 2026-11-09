"""
PEA AMR reports (โครงการพัฒนาการอ่านหน่วยด้วยระบบอัตโนมัติ): 15-minute kWh per meter, per TOU rate -- owner, 9 Nov 2026.

The report downloads as "kWh<CA><timestamp>.xls", which is really an HTML page: a header (month, CA, meter number) and one row per
15 minutes, "dd/mm/yyyy hh.mm" = the END of the slot ("24.00" closes the day), with the kWh in the RATE A (on-peak), B (weekday
off-peak) or C (weekend / holiday) column. Saraburi's reports are already billed kWh (they match the bill to a few units), so
multiplier = 1.

* parse(html) -> {"month", "ca_no", "meter_no", "rows": [{"slot_start", "tou", "kwh"}]}; month None for a report with no data.
"""
import re
from datetime import datetime, timedelta

THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม",
               "พฤศจิกายน", "ธันวาคม"]
ROW = re.compile(r"&nbsp;(\d\d)/(\d\d)/(\d{4}) (\d\d)\.(\d\d)</td>(.*?)</tr>", re.S)
CELL = re.compile(r"<td class='rightdetail'>([^<]*)</td>")


class NotAmr(ValueError):
    pass


def _field(html: str, label: str):
    m = re.search(label + r"\s*:\s*</td>\s*<td>([^<&]*)", html)
    return m.group(1).strip() if m else None


def parse(html: str) -> dict:
    if "กิโลวัตต์ชั่วโมง" not in html:
        raise NotAmr("ไม่ใช่รายงาน AMR ของ กฟภ.")
    m = re.search(r"ประจำเดือน : (\S+) (\d{4})\]", html)
    month = f"{int(m.group(2)) - 543}-{THAI_MONTHS.index(m.group(1)) + 1:02d}" if m and m.group(1) in THAI_MONTHS else None
    rows = []
    for d, mo, y, hh, mm, rest in ROW.findall(html):
        end = datetime(int(y), int(mo), int(d)) + timedelta(hours=int(hh), minutes=int(mm))
        cells = CELL.findall(rest)[:3]
        for i, v in enumerate(cells):
            if v.strip():
                rows.append({"slot_start": (end - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:00+07"), "tou": "ABC"[i], "kwh": float(v)})
                break
    return {"month": month if rows else None, "ca_no": _field(html, "บัญชีผู้ใช้ไฟ"), "meter_no": _field(html, "หมายเลขมิเตอร์"), "rows": rows}
