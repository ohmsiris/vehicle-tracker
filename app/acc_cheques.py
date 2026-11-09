"""
บัญชี › เช็คจาก HQ: the monthly cheque summary HQ sends ("ใบสรุปจ่ายเช็คประจำเดือนMM-69-สระบุรี+แก่งคอย.xls", one sheet per branch).

Each row becomes one entry paid from the HQ bank (1130) -- or from คุณรัชนิดา's own account (1120) when the row says it was withdrawn
from her book (the cash for petty cash, mid-month advances, cash wages) -- sorted by keyword rules:
* money sent to the branch's petty cash (เงินเดือนสด + ค่าใช้จ่ายสิ้นเดือน, คชจ. + เบิกกลางเดือน, ถอนสด ...): not a cost -- cash 1100
  goes up; the costs themselves come from the mid / end-of-month documents, so nothing is counted twice;
* utilities, bags, chemicals, salt, machine parts ...: the cost, with 7% Input VAT split off when the payee is a company / PEA
  (they issue tax invoices; individuals usually do not);
* the accountant's row is split by its note (บริการ=, ภงด.53=, vat=): fee / withholding tax remitted / VAT paid;
* the owner's own payments (ผ่อนคอนโด): 3200 owner's drawings, not a cost;
* money lent / returned between branches or companies (ยืม ...): 1600, not a cost;
* a cheque that pays a stored PEA bill clears that bill (to the satang, or within 1,000 with the difference flagged);
* anything the rules do not know goes to 7900 flagged.
Accrual date: a row with its own day (12/9/69) uses it; a row that names its month ("08/69") is booked on the 25th of that month;
other supplier bills are last month's bills (the run on the 20th pays them), so the 25th of the month before the run.
parse_sheet / classify / accrual_date / build are pure and unit-tested.
"""
import re
from datetime import date, timedelta
from typing import Optional

from app import supabase

FUNDING = re.compile(r"เงินเดือนสด|ค่าใช้จ่ายสิ้นเดือน|คชจ\.?\s*\+?\s*เบิกกลางเดือน|ถอนสด|ถอนเงินสด|เบิกกลางเดือนไม่พอ|สำรอง")
OWNER = re.compile(r"คอนโด")                                # the owner's own payments (คุณรัชนิดา's condo): drawings, not a company cost
TRANSFER = re.compile(r"ยืม|คืนหัวหิน|คืนเงินเหรียญ")    # money moved between branches / companies: owed back, not a cost
RULES = [  # (pattern on the lower-cased description, account) -- first match wins
    (r"ค่าไฟฟ้า|ไฟฟ้าแก่งคอย\s*\d", "5100"),       # the PEA bill (not "...รอกไฟฟ้า", "ตรวจระบบไฟฟ้า", a transformer repair)
    (r"เงินเดือน", "7100"),
    (r"ค่าน้ำแข็ง", "5050"),
    (r"ชลประทาน", "5200"),
    (r"ประปา", "5210"),
    (r"เกลือ", "5300"),
    (r"แอมโมเนีย", "5310"),
    (r"pac|คลอรีน|โพลิเมอร์|sunfilt|สารกรอง|แอนทราไซ|คาร์บอน", "5320"),
    (r"กระสอบ|ถุง|เชือก", "5330"),
    (r"ประกันสังคม", "7110"),
    (r"ต่างด้าว|พม่า|พาสปอร์ต", "7120"),
    (r"ต่อภาษี|พรบ|ลักษณะตัวรถ|ขนส่ง", "6300"),
    (r"ค่าสอบบัญชี", "7600"),
    (r"gps", "7200"),
    (r"ซ้อมดับเพลิง|เปิดหน้าบัญชี", "7500"),
    (r"น้ำมันคอม|valve|วาล์ว|วาร์ว|ไส้กรอง|กรองน้ำมัน|กระเปาะ|สายพาน|\bpm\b|มอเตอร์|มอร์เตอร์|ผ้าเบรค|ค่าแรง|ค่าช่าง|มาดูเครื่อง|"
     r"คอมม์|คอมเพรสเซอร์|ห้องเย็น|โอเวอร์ฮอล|มู่เล่|แดนฟอ|อะไหล่|รอก|เครน|ยางยอย|หม้อแปล|ตรวจระบบ|siphon|strainer|ซ่อม|"
     r"คอยเย็น|พัดลม|สวิทซ์|ล้างคอนเดนเซอร์|ตะ?กรัน|ตะกัน|c200|แก้ไขเครื่อง|สลิง", "5500"),
    (r"ถัง\s*\d+\s*ลิตร|ม่าน|ประกับ|พาเลท", "5510"),
]
NO_VAT = {"7100", "7110", "7120", "5050", "6300"}          # wages, government, family ice, vehicle tax: no tax invoice
VAT_PAYEE = re.compile(r"บริษัท|บจ\.|บจก|หจก|ห้างหุ้นส่วน|การไฟฟ้า|ไฟฟ้า|แคชเชียร์เช็ค")
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
MONTH_TAG = re.compile(r"(?<!\d)(\d{1,2})\s*[/\-]\s*(?:25)?(6\d)(?!\d)")
DAY_TAG = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(?:25)?(6\d)(?!\d)")


def be_date(v) -> Optional[date]:
    """A cheque date: '15/09/2569' or an Excel serial typed in the Buddhist year (244592 = 31 Aug 2569 -> 2026-08-31)."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        d = date(1899, 12, 30) + timedelta(days=int(v))
        return d.replace(year=d.year - 543) if d.year > 2400 else d
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(v).strip())
    if m:
        y = int(m.group(3))
        return date(y - 543 if y > 2400 else y, int(m.group(2)), int(m.group(1)))
    return None


def parse_sheet(rows: list[list]) -> list[dict]:
    """Rows of one branch sheet -> [{no, what, payee, amount, cheque_date, cheque_no, bank, note}] (header, totals, notes skipped)."""
    out = []
    for r in rows:
        r = list(r) + [""] * (12 - len(r))
        try:
            no = int(float(r[0]))
        except (TypeError, ValueError):
            continue
        amount = 0.0
        for c in (r[3], r[4]):
            try:
                amount += float(c or 0)
            except (TypeError, ValueError):
                pass
        if amount <= 0:
            continue
        cno = r[6]
        cno = str(int(cno)) if isinstance(cno, float) else str(cno or "").strip()
        out.append({"no": no, "what": str(r[1] or "").strip(), "payee": str(r[2] or "").strip(), "amount": round(amount, 2),
                    "cheque_date": be_date(r[5]), "cheque_no": cno or None, "bank": str(r[7] or "").strip(), "note": str(r[11] or "").strip()})
    return out


def _note_split(note: str) -> dict:
    found = {}
    for key, pat in (("fee", r"บริการ\s*=?\s*([\d,\.]+)"), ("wht", r"ภงด\.?\s*\d*\s*=\s*([\d,\.]+)"), ("vat", r"vat\s*=?\s*([\d,\.]+)")):
        m = re.search(pat, note, re.I)
        if m:
            found[key] = float(m.group(1).replace(",", "").rstrip("."))
    return found


def accrual_date(row: dict, file_month: date) -> tuple[date, bool]:
    """(date, estimated?)."""
    m = DAY_TAG.search(row["what"])
    if m:
        return date(2500 + int(m.group(3)) - 543, int(m.group(2)), int(m.group(1))), False
    m = MONTH_TAG.search(row["what"])
    if m and 1 <= int(m.group(1)) <= 12:
        return date(2500 + int(m.group(2)) - 543, int(m.group(1)), 25), False
    return (file_month.replace(day=1) - timedelta(days=1)).replace(day=25), True


def classify(row: dict) -> dict:
    """{kind: funding / accounting / cost / unknown, account, vat}."""
    what = row["what"]
    if FUNDING.search(what):
        return {"kind": "funding", "account": "1100", "vat": 0.0}
    if OWNER.search(what):
        return {"kind": "owner", "account": "3200", "vat": 0.0}
    if "ตัดธนาคาร" in what:     # Kaeng Khoi paying back Saraburi's account for its staff paid by bank transfer: the wages are in payroll
        return {"kind": "transfer", "account": "1600", "vat": 0.0}
    if TRANSFER.search(what) and "เงินเดือน" not in what:
        return {"kind": "transfer", "account": "1600", "vat": 0.0}
    if "ค่าทำบัญชี" in what:
        return {"kind": "accounting", "account": "7600", "vat": 0.0, "split": _note_split(row["note"])}
    for pat, acc in RULES:
        if re.search(pat, what.lower()):
            vat = round(row["amount"] * 7 / 107, 2) if acc not in NO_VAT and VAT_PAYEE.search(f"{row['payee']} {what}") else 0.0
            return {"kind": "cost", "account": acc, "vat": vat}
    return {"kind": "unknown", "account": "7900", "vat": 0.0}


def other_branch(row: dict, branch: str) -> Optional[str]:
    """Ice bought from the company's other branch ("ค่าน้ำแข็งแก่งคอย" on Saraburi's sheet, "ค่าน้ำแข็งสระบุรี" on Kaeng Khoi's). The ice
    itself is booked from the ice program's sales report (app/acc_sales: 4150 where it was made, 5060 where it went, on the delivery
    date); the cheque only settles what the branch owes the other one (Dr 1600). Ice from บางปะอิน stays 5050, bought from outside."""
    names = {"สระบุรี": "แก่งคอย", "แก่งคอย": "สระบุรี"}
    other = names.get(branch)
    return other if other and other in row["what"] else None


def paid_from(row: dict) -> str:
    """Which account the money left: a row withdrawn from คุณรัชนิดา's own book (เงินเดือนสด, คชจ. + เบิกกลางเดือน -- the cash side)
    is her account 1120; everything else is the HQ account 1130 (company payments, VAT bills, bank-transfer salaries)."""
    # "ถอนจากเล่ม" only: a cheque paid TO คุณรัชนิดา (ice between branches) is still from the HQ account
    return "1120" if re.search(r"ถอนจากเล่ม|เล่มชื่อ", row.get("payee") or "") else "1130"


def _rid(no: int, seen: dict) -> str:
    """Row id inside a batch: the sheet's own number, which HQ sometimes repeats -- then r16, r16b, r16c ..."""
    seen[no] = seen.get(no, 0) + 1
    return f"r{no}" + ("" if seen[no] == 1 else chr(ord("a") + seen[no] - 1))


def _inv(v):
    return v[0] if isinstance(v, tuple) else v


def build(rows: list[dict], *, branch: str, file_month: date, batch: str, bills: Optional[dict] = None) -> dict:
    """bills = {total: invoice no.} of PEA bills already stored: a cheque for exactly that amount pays the bill (Dr 2100) instead of
    being the cost again."""
    entries, flags, seen = [], [], {}
    bills = bills or {}
    for r in rows:
        c = classify(r)
        bank = paid_from(r)
        other = other_branch(r, branch) if c["account"] == "5050" else None
        if other:
            c = {"kind": "settles_branch", "account": "1600", "vat": 0.0}
        when, est = accrual_date(r, file_month)
        amt = r["amount"]
        inv, owed = None, amt
        if c["account"] == "5100":
            inv = _inv(bills.get(round(amt, 2)))
            if not inv:      # a cheque a little off the bill (a typo, a fee): still that bill, the difference shown and flagged
                near = [(abs(t - amt), t, _inv(v)) for t, v in bills.items()
                        if abs(t - amt) <= 1000.01 and (not isinstance(v, tuple) or v[1] == when.strftime("%Y-%m"))]
                if near:
                    _, owed, inv = min(near)
                    flags.append(f"แถว {r['no']}: เช็ค {amt:,.2f} ไม่ตรงใบแจ้ง PEA {inv} ({owed:,.2f}) ต่าง {amt - owed:,.2f} — ลง 'อื่นๆ' ไว้")
        if inv:
            c = {"kind": "pays_bill", "account": "2100", "vat": 0.0}
            when, est = r["cheque_date"] or when, False
            diff = round(amt - owed, 2)
            lines = [("2100", owed, 0.0)] + ([("7900", diff, 0.0)] if diff > 0 else [("7900", 0.0, -diff)] if diff < 0 else []) + [(bank, 0.0, amt)]
        elif c["kind"] == "transfer":
            when, est = r["cheque_date"] or file_month.replace(day=15), False
            lines = [("1600", amt, 0.0), (bank, 0.0, amt)]
        elif c["kind"] == "funding":
            when, est = r["cheque_date"] or file_month.replace(day=15), False
            lines = [("1100", amt, 0.0), (bank, 0.0, amt)]
        elif c["kind"] == "accounting":
            s = c["split"]
            fee, wht, vat = s.get("fee", 0.0), s.get("wht", 0.0), s.get("vat", 0.0)
            rest = round(amt - fee - wht - vat, 2)
            if abs(rest) >= 1 and fee:     # the note's VAT is sometimes the cheque total copied in: VAT is what is left
                flags.append(f"แถว {r['no']}: ค่าทำบัญชี — VAT ในหมายเหตุ ({vat:,.2f}) ไม่ลงตัว ใช้ส่วนที่เหลือ {amt - fee - wht:,.2f}")
                vat, rest = round(amt - fee - wht, 2), 0.0
            lines = [("7600", fee + rest, 0.0)] + ([("2200", wht, 0.0)] if wht else []) + ([("2300", vat, 0.0)] if vat else []) + [(bank, 0.0, amt)]
            if abs(rest) >= 1:
                flags.append(f"แถว {r['no']}: ค่าทำบัญชี แยกได้ไม่ครบ (ส่วนที่เหลือ {rest:,.2f} รวมไว้ในค่าทำบัญชี)")
        else:
            vat = c["vat"]
            lines = [(c["account"], round(amt - vat, 2), 0.0)] + ([("1400", vat, 0.0)] if vat else []) + [(bank, 0.0, amt)]
            if c["kind"] == "unknown":
                flags.append(f"แถว {r['no']}: {r['what']} ({amt:,.2f}) — ไม่รู้หมวด ใส่ 'อื่นๆ' ไว้ก่อน")
        desc = " · ".join(x for x in (r["what"], f"จ่ายใบแจ้ง PEA {inv}" if inv else "", f"เช็ค {r['cheque_no']}" if r["cheque_no"] else "", r["note"],
                                       "วันที่ประมาณ (บิลเดือนก่อน)" if est else "") if x)
        rid = _rid(r["no"], seen)
        entries.append({"entry_date": when.isoformat(), "branch": branch, "source": "hq_cheques", "source_id": f"{batch}:{rid}",
                        "party": (r["payee"] or "HQ")[:100], "description": desc[:300], "doc_type": "tax_invoice" if c.get("vat") else "cheque",
                        "doc_no": r["cheque_no"], "kind": c["kind"],
                        "lines": [{"account": a, "debit": round(d, 2), "credit": round(cr, 2), "branch": branch, "vehicle_id": None,
                                   "machine_id": None, "memo": None} for a, d, cr in lines]})
    return {"entries": entries, "flags": flags}


def read_xls(path: str) -> dict:
    """{sheet name: rows} of an .xls / .xlsx file."""
    if path.lower().endswith(".xls"):
        import xlrd
        book = xlrd.open_workbook(path)
        return {sh.name: [[c.value for c in sh.row(i)] for i in range(sh.nrows)] for sh in book.sheets()}
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}


def run_month(rows: list[list]) -> Optional[date]:
    """The cheque run's month from the sheet's title row ("... ประจำเดือน กันยายน 2569") -- file and sheet names are sometimes stale."""
    for r in rows[:3]:
        text = " ".join(str(c) for c in r if c)
        for i, name in enumerate(THAI_MONTHS):
            m = re.search(name + r"\s*(25\d\d)", text)
            if m:
                return date(int(m.group(1)) - 543, i + 1, 1)
    return None


def import_file(path: str, file_month: Optional[date] = None, branch: str = "สระบุรี", write: bool = False) -> dict:
    from app.accounting import _insert_entries, _require
    sheets = read_xls(path)
    want_kk = branch == "แก่งคอย"
    name = next((n for n in sheets if ("แก่งคอย" in n) == want_kk), None)
    if not name:
        raise ValueError("ไม่พบชีทของสาขานี้")
    file_month = run_month(sheets[name]) or file_month
    if not file_month:
        raise ValueError("ไม่รู้เดือนของรอบเช็ค")
    batch = f"cheque-{file_month:%Y-%m}-{branch}"
    bills = {}
    try:
        bills = {round(float(x["total"]) - float(x.get("deposit_interest") or 0), 2): (x["invoice_no"], x["bill_period"])   # what was paid
                 for x in supabase.select_all("elec_bills", {"select": "total,deposit_interest,invoice_no,bill_period", "order": "invoice_no.asc"})}
    except supabase.SupabaseError:
        pass
    out = build(parse_sheet(sheets[name]), branch=branch, file_month=file_month, batch=batch, bills=bills)
    out["sheet"], out["month"] = name, file_month
    if write:
        _require()
        supabase.delete("acc_entries", {"source_id": f"like.{batch}:*"})
        _insert_entries(out["entries"])
    return out
