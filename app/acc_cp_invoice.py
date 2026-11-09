"""
Kaeng Khoi's CP / CPF revenue from the tax-invoice book (what was actually billed), not from the ice program -- owner, 9 Nov 2026.

The program logs every CP / CPF delivery but several plants carry a price of 0 (CP โกลบอล, CP ศูนย์จำหน่าย from April, CPF หนองโดน,
CPF อปร2), so the program under-states CPF revenue by 100-250k a month. CP ซิลเลอร์ agrees with the invoices within ~3%.

* read_book(path): {calendar month: {"cp" | "cpf_other": baht}} from "สรุปใบกำกับภาษี แก่งคอย.xlsx" (one sheet per month).
* build(book, sales, last_month): accrual entries per company month (26-25). A calendar month's invoiced total is spread over its delivery
  days (CP ซิลเลอร์ by the program's baht, the other plants by number of deliveries); days 26-31 belong to the next company month.
  A calendar month the book does not have yet is estimated: CP = the program's baht; other plants = the program's baht + the average
  shortfall of the last three invoiced months. Those entries say "ประมาณ" and are replaced when the invoice sheet arrives.
"""
import re
from collections import defaultdict
from datetime import date

BRANCH = "แก่งคอย"
THAI_LONG = {"มกรา": 1, "กุมภา": 2, "มีนา": 3, "เมษา": 4, "พฤษภา": 5, "มิถุนา": 6, "กรกฎา": 7, "สิงหา": 8, "กันยา": 9, "ตุลา": 10,
             "พฤศจิ": 11, "ธันวา": 12}
THAI_SHORT = {"ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย": 4, "พ.ค.": 5, "มิ.ย.": 6, "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10,
              "พ.ย.": 11, "ธ.ค.": 12}
LABEL = {"cp": "CPF ซิลเลอร์ (CP 4001)", "cpf_other": "CPF โรงงานอื่น (โรงหมู โกลบอล ศูนย์จำหน่าย หนองโดน อปร.)"}


def channel(customer: str):
    """Program customer -> invoice channel, or None (not CP)."""
    c = (customer or "").strip()
    if "ซิลเลอร์" in c:
        return "cp"
    if c.upper().startswith("CP"):
        return "cpf_other"
    return None


def invoice_channel(name: str):
    if "บางปะอิน" in name or "อ่างทอง" in name:
        return None
    if "ซิลเลอร์" in name:
        return "cp"
    if name.startswith("CP") or any(w in name for w in ("อปร", "ใบลดหนี้", "โรงหมู", "ศูนย์จำหน่าย", "หนองโดน", "โกลบอล")):
        return "cpf_other"
    return None


def sheet_month(title: str):
    t = title.strip()
    for k, v in THAI_LONG.items():
        if t.startswith(k):
            y = re.search(r"25(\d\d)", t)
            return f"{2500 + int(y.group(1)) - 543 if y else 2026}-{v:02d}"
    for k, v in THAI_SHORT.items():
        if t.startswith(k):
            return f"{2500 + int(re.findall(r'\d\d', t)[-1]) - 543}-{v:02d}"
    return None


def read_book(path: str) -> dict:
    import openpyxl
    out: dict = defaultdict(lambda: defaultdict(float))
    for ws in openpyxl.load_workbook(path, data_only=True).worksheets:
        m = sheet_month(ws.title)
        if not m:
            continue
        for r in ws.iter_rows(min_row=3, values_only=True):
            if len(r) < 6 or not isinstance(r[3], (int, float)) or not isinstance(r[5], (int, float)):
                continue
            ch = invoice_channel(str(r[2]))
            if ch:
                out[m][ch] += r[5]
    return {m: dict(v) for m, v in out.items()}


def company_month(d: date) -> str:
    if d.day <= 25:
        return f"{d.year}-{d.month:02d}"
    return f"{d.year + 1}-01" if d.month == 12 else f"{d.year}-{d.month + 1:02d}"


def build(book: dict, sales: list[dict], first: str, last: str) -> list[dict]:
    """sales = acc_sales.lines() of Kaeng Khoi (sale_date ISO). Entries for company months first..last."""
    weight: dict = defaultdict(lambda: defaultdict(float))     # (cal month, channel) -> {date: weight}
    prog: dict = defaultdict(float)                            # (cal month, channel) -> program baht
    for s in sales:
        ch = channel(s["customer"])
        if not ch:
            continue
        d = date.fromisoformat(s["sale_date"]) if isinstance(s["sale_date"], str) else s["sale_date"]
        cal = f"{d.year}-{d.month:02d}"
        weight[(cal, ch)][d] += float(s["amount"] or 0) if ch == "cp" else 1.0
        prog[(cal, ch)] += float(s["amount"] or 0)
    invoiced = sorted(book)
    gap = {}
    for ch in ("cp", "cpf_other"):
        recent = [m for m in invoiced if (m, ch) in prog][-3:]
        gap[ch] = 0.0 if ch == "cp" or not recent else sum(book[m].get(ch, 0) - prog[(m, ch)] for m in recent) / len(recent)
    per: dict = defaultdict(float)                 # (company month, channel, estimated) -> baht
    for (cal, ch), days in weight.items():
        est = cal not in book
        total = book[cal].get(ch, 0.0) if not est else prog[(cal, ch)] + gap[ch]
        w = sum(days.values())
        if not total or not w:
            continue
        for d, x in days.items():
            per[(company_month(d), ch, est)] += total * x / w
    out = []
    for (cm, ch, est), amt in sorted(per.items()):
        if not (first <= cm <= last):
            continue
        amt = round(amt, 2)
        y, m = (int(x) for x in cm.split("-"))
        out.append({"entry_date": date(y, m, 25).isoformat(), "branch": BRANCH, "source": "sales_program",
                    "source_id": f"sales:{BRANCH}:{cm}:invoice:{ch}" + (":est" if est else ""), "party": LABEL[ch],
                    "doc_type": "tax_invoice", "doc_no": None,
                    "description": (f"ขายน้ำแข็ง {LABEL[ch]} {m:02d}/{y + 543} · "
                                    + ("ประมาณ: ยังไม่มีใบกำกับเดือนนี้ ใช้ยอดจากโปรแกรม + ส่วนที่โปรแกรมไม่มีราคา (เฉลี่ย 3 เดือน)" if est
                                       else "ตามสรุปใบกำกับภาษี กระจายตามวันส่งของ")),
                    "lines": [{"account": "1200", "debit": amt, "credit": 0.0, "branch": BRANCH, "vehicle_id": None, "machine_id": None, "memo": None},
                              {"account": "4100", "debit": 0.0, "credit": amt, "branch": BRANCH, "vehicle_id": None, "machine_id": None, "memo": None}]})
    return out
