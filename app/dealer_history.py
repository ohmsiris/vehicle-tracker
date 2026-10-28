"""
The dealership's "ประวัติการซ่อม" report (Isuzu service system, PDF) read into visits and lines, so a truck's whole history can be loaded without
photographing every bill. No prices are in this report: it says WHAT was done, on which day, at which odometer.

The PDF has a text layer. Its Thai has one quirk: the sara-am is written as a floating nikhahit ("นÎ้ามัน" for น้ำมัน), which normalise() puts right.
Layout, in reading order: a header block per page (plate, VIN, engine no ... down to the column titles "ค้างซ่อม"), then visits:
    <date in> <date of invoice> <odometer>            (a visit continued on the next page repeats these three lines)
    then per line: <type: ค่าแรง / อะไหล่ / วัสดุสิ้นเปลือง ...> <description> [<explanation, 1-3 lines>] [<qty> <unit>] [" " = empty cell]
and a footer per page ("วัน/เวลาที่พิมพ์ ... หน้า n / m"). Years are พ.ศ.

Pure functions (normalise, parse_report_text) are unit-tested; reading the PDF itself is left to the caller (scripts/import_dealer_history.py).
"""
import re
from datetime import date
from typing import Optional

DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
MILEAGE_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d+$")
QTY_RE = re.compile(r"^(\d+(?:\.\d+)?)(?:\s+(\S+))?$")
TYPES = {"ค่าแรง", "อะไหล่", "วัสดุสิ้นเปลือง", "งานนอก", "ค่าบริการ"}
HEADER_END = "ค้างซ่อม"                     # the last column title: the header block of a page ends with it
PLATE_RE = re.compile(r"^(\d?[ก-ฮ]{1,3}\d{1,4})\s+([ก-ฮ]{2,})$")


def normalise(text: str) -> str:
    """Put the floating sara-am right: 'นÎ้ามัน' -> 'น้ำมัน', 'ทÎาความ' -> 'ทำความ'."""
    text = text.replace("Î", "ํ")
    text = re.sub("ํ([่-๋])า", r"\1ำ", text)
    return text.replace("ํา", "ำ")


def _ce(d: str) -> Optional[date]:
    m = DATE_RE.match(d.strip())
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(year - 543 if year > 2400 else year, month, day)
    except ValueError:
        return None


def parse_report_text(text: str) -> dict:
    """{plate, province, vin, engine_no, visits: [{date, invoice_date, mileage, lines: [{kind, description, note, quantity, unit}]}], unparsed: [...]}"""
    raw = [ln.rstrip("\n") for ln in normalise(text).split("\n")]
    out = {"plate": None, "province": None, "vin": None, "engine_no": None, "visits": [], "unparsed": []}
    i, n = 0, len(raw)
    cur_visit: Optional[dict] = None
    cur_line: Optional[dict] = None

    def close_line():
        nonlocal cur_line
        if cur_line is not None and cur_visit is not None:
            note = " ".join(cur_line.pop("note_parts")).replace("*", " ")
            cur_line["note"] = re.sub(r"\s+", " ", note).strip() or None
            cur_visit["lines"].append(cur_line)
        cur_line = None

    while i < n:
        s = raw[i].strip()
        if s.startswith("#####") or not s and raw[i] == "":
            i += 1
            continue
        if s == "ประวัติการซ่อม":                                    # a page header: read the plate / VIN, skip to the end of the column titles
            close_line()
            j = i
            while j < n and raw[j].strip() != HEADER_END:
                m = PLATE_RE.match(raw[j].strip())
                if m and not out["plate"]:
                    out["plate"], out["province"] = m.group(1), m.group(2)
                if re.fullmatch(r"[A-Z0-9]{17}", raw[j].strip()) and not out["vin"]:
                    out["vin"] = raw[j].strip()
                if raw[j].strip() == "เลขครื่อง" and j + 1 < n and not out["engine_no"]:
                    out["engine_no"] = raw[j + 1].strip()
                j += 1
            i = j + 1
            continue
        if s.startswith("วัน/เวลาที่พิมพ์") or re.match(r"^หน้า\s*\d+\s*/\s*\d+$", s):         # the footer
            close_line()
            i += 1
            continue
        d1 = _ce(s)
        if d1 and i + 2 < n and _ce(raw[i + 1]) and MILEAGE_RE.match(raw[i + 2].strip()):
            close_line()
            d2, mileage = _ce(raw[i + 1]), int(raw[i + 2].strip().replace(",", ""))
            if cur_visit and cur_visit["date"] == d1 and cur_visit["mileage"] == mileage:
                pass                                                 # the same visit continued on the next page
            else:
                cur_visit = {"date": d1, "invoice_date": d2, "mileage": mileage, "lines": []}
                out["visits"].append(cur_visit)
            i += 3
            continue
        if s in TYPES and cur_visit is not None:
            close_line()
            desc = raw[i + 1].strip() if i + 1 < n else ""
            cur_line = {"kind": s, "description": desc, "note_parts": [], "quantity": None, "unit": None}
            i += 2
            continue
        if cur_line is not None:
            if not s:
                pass                                                 # an empty cell
            elif QTY_RE.match(s) and cur_line["quantity"] is None and (cur_line["kind"] != "ค่าแรง"):
                m = QTY_RE.match(s)
                cur_line["quantity"], cur_line["unit"] = float(m.group(1)), m.group(2)
            else:
                cur_line["note_parts"].append(s)
            i += 1
            continue
        out["unparsed"].append(s)
        i += 1
    close_line()
    return out


def line_text(line: dict) -> str:
    """The line as it is stored: the description, then the dealer's explanation in brackets ('ตรวจสอบช่วงล่างทั้งหมด (ปกติ)')."""
    return f"{line['description']} ({line['note']})" if line.get("note") else line["description"]
