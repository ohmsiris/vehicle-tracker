"""
Per-person monthly pay for the Saraburi drivers (SB-PD), from HQ's payroll file "เงินเดือน พขร. สระบุรี <month> 2569 -.xlsx"
(sheet "ตารางรายรับ-จ่ายพนง.") or, in months HQ only sent a PDF, the summary PDF "สรุป… / ใบสรุป… พขร. สระบุรี …" -- the same
table printed. Only the main per-person table is read: never the slip sheets, never the bank-account / transfer columns
(everything right of the deduction columns is left alone), never cell comments.

Columns (found by their header text, so a moved column does not matter):
  วัน | เงิน (wage = days x rate) | [sales, no header] | เงิน (commission = sales x %) | ซอง% (bag money + the 1,000 เบี้ยขยัน)
  รวมรับ | รวมจ่าย | คงเหลือ | deductions: สะสม เบิกล่วงหน้า เงินยืม ส่วนกลาง ทำบัตร เงินขาด/กระสอบไม่ครบ ความผิด(อุปกรณ์)/อื่นๆ ปกส.
  January's sheet has no ทำบัตร / อื่นๆ column (เงินขาด/ความผิด/กระสอบ is one column then).

เบี้ยขยัน (attendance bonus, 1,000) has no column of its own in Saraburi: it is inside ซอง%.
  xlsx: bonus when the ซอง% formula adds 1000 (=497*3+1000, =1000+28*10, =1000) or the cell is exactly 1000.
  PDF:  bonus when ซอง% >= 1000 and the row is not marked "ไม่ให้เบี้ยขยัน" -- a guess, flagged. One refinement, checked against every
        xlsx formula: when ซอง% is more than 1000 (bag drivers PD001/002/006) and the days are short of the company month (26th-25th),
        the bonus was not paid, so it is 0 (flagged too).
The driver sheet's commission column is always sales x % -> parts.commission (drivers have no OT).
Money in the pay columns that is not pay for the month (a savings refund) comes only from OVERRIDES (owner-confirmed); typed
ซอง% amounts that look like one are flagged, not moved.

parse_file(path, month) -> list[dict] (see README of the payroll-people modules); parse_xlsx_rows / parse_pdf_words are pure.
"""
import re
from datetime import date

GROUP = "drivers"
BONUS = 1000.0
BIG_BAG = 5000.0           # real bag money is under ~4,000 a month (PD001 ~3,700); more than this is a refund / back pay in disguise
OVERRIDES = {("2026-09", "SB-PD025"): {"refund": 20500.0}}     # owner-confirmed: his savings paid back, out of ซอง%

DEDUCTION_KEYS = ["savings", "advance", "loan", "common", "card", "shortage", "other", "sso"]
PART_KEYS = ["wage", "commission", "bag", "bonus", "ot", "other"]
NO_BONUS = "ไม่ให้เบี้ยขยัน"
CODE = re.compile(r"^SB-[A-Z]{2}\d+[A-Z]?$")
NUM = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
ROLE = re.compile(r"(สแปร์-?)?(พขร\.|ขับรถ|เด็กรถ|พนักงาน)")     # not "(พขร)" inside a name
MARKS = "ัิ-ฺ็-๎"          # Thai vowels / tone marks that sit above or below a consonant


def deduction_key(label: str):
    """Header text -> deduction key (None for anything that is not one of the 8 deduction columns)."""
    t = re.sub(r"\s+", "", str(label or ""))
    if not t:
        return None
    if t.startswith("สะสมยก") or t.startswith("ค้าง") or t.startswith("จ่ายค้าง"):
        return None
    if t == "สะสม":
        return "savings"
    if "เบิกล่วงหน้า" in t:
        return "advance"
    if "เงินยืม" in t or "คืนเงินกู้" in t:
        return "loan"
    if "ส่วนกลาง" in t:
        return "common"
    if "บัตร" in t or "passport" in t.lower():
        return "card"
    if "เงินขาด" in t or "กระสอบ" in t:
        return "shortage"
    if "อื่น" in t or "อุปกรณ์" in t or "ความผิด" in t:
        return "other"
    if "ปกส" in t or "ประกันสังคม" in t:
        return "sso"
    return None


def period_days(month: str) -> int:
    """Days in the company month (26th of the month before .. 25th)."""
    y, m = (int(x) for x in month.split("-"))
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    return (date(y, m, 25) - date(py, pm, 26)).days + 1


def fix_thai(text: str) -> str:
    """PDF text splits sara-am off its consonant ("ก ำจันทร์", or "ก าจันทร์" in files whose font maps ำ to า; "หม ่ำ"):
    a word that starts with marks + า/ำ belongs to the word before, and that า is the ำ."""
    text = re.sub(rf"\s+([{MARKS}]*)[าำ]", lambda m: m.group(1) + "ำ", text)
    text = re.sub(rf"\s+([{MARKS}])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _num(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _bonus_from_formula(formula, value: float) -> bool:
    if abs(value - BONUS) < 0.005:
        return True
    if isinstance(formula, str) and formula.startswith("="):
        return re.search(r"(^=|\+)\s*1000(?:\.0+)?\s*(\+|$)", formula.replace(" ", "")) is not None
    return False


def build(month: str, code: str, name: str, dept: str, days: float, wage: float, sales: float, commission: float,
          bag_col: float, gross: float, deduct_total: float, net: float, deductions: dict, source: str,
          bonus: float, flags: list, all_deductions: bool = True) -> dict:
    """One person-month in the output shape. bag_col = the whole ซอง% cell; bonus already decided."""
    flags = list(flags)
    refund = float(OVERRIDES.get((month, code), {}).get("refund", 0.0))
    bag = bag_col - refund - bonus
    if refund and bag < -0.005:
        flags.append(f"refund {refund:,.0f} is more than ซอง% {bag_col:,.0f}")
    if bag > BIG_BAG and not refund and not any(f.startswith("ซอง% typed") for f in flags):
        hint = " and the name says คืนสะสม" if "คืนสะสม" in name.replace(" ", "") else ""
        flags.append(f"ซอง% {bag_col:,.0f} is far above normal bag pay{hint}: savings refund / back pay? counted as bag pay")
    parts = {"wage": round(wage, 2), "commission": round(commission, 2), "bag": round(bag, 2), "bonus": bonus, "ot": 0.0, "other": 0.0}
    rest = round(gross - sum(parts.values()) - refund, 2)
    if abs(rest) > 0.99:
        parts["other"] = rest
        flags.append(f"pay columns do not add up to รวมรับ: {rest:,.2f} put in other")
    if abs(gross - deduct_total - net) > 1:
        flags.append(f"รวมรับ - รวมจ่าย != คงเหลือ ({gross - deduct_total - net:,.2f})")
    ded = {k: round(float(deductions.get(k, 0.0)), 2) for k in DEDUCTION_KEYS}
    if all_deductions and abs(sum(ded.values()) - deduct_total) > 1:
        flags.append(f"deduction columns add up to {sum(ded.values()):,.2f}, รวมจ่าย {deduct_total:,.2f}")
    if commission and not sales:
        flags.append("commission with no sales figure")
    return {"month": month, "group": GROUP, "code": code, "name": name, "dept": dept, "days": float(days), "parts": parts,
            "refund": refund, "gross": round(gross, 2), "deductions": ded, "deduct_total": round(deduct_total, 2),
            "net": round(net, 2), "source": source, "flags": flags}


# ---------------------------------------------------------------- xlsx

def header_columns(header_rows: list[list]) -> dict:
    """header_rows = rows 2..4 of the sheet (lists of cell values from column A). -> {field: column index (0-based)};
    deduction columns under "ded" as {key: index}. Raises ValueError if the table is not the drivers' table."""
    cols, ded = {}, {}
    width = max(len(r) for r in header_rows)
    cell = lambda r, c: re.sub(r"\s+", "", str(header_rows[r][c])) if c < len(header_rows[r]) and header_rows[r][c] is not None else ""
    for c in range(width):
        texts = [cell(r, c) for r in range(len(header_rows))]
        for t in texts:
            if t.lower().startswith("scan") and "code" not in cols:
                cols["code"] = c
            elif t == "รายชื่อ":
                cols["name"] = c
            elif t == "วัน":
                cols["days"] = c
            elif t == "ซอง%":
                cols["bag"] = c
            elif t == "รวมรับ":
                cols["gross"] = c
            elif t == "รวมจ่าย":
                cols["deduct"] = c
            elif t == "คงเหลือ":
                cols["net"] = c
            elif t == "เงิน":
                cols.setdefault("money", []).append(c)
    if not {"code", "name", "days", "bag", "gross", "deduct", "net"} <= cols.keys() or len(cols.get("money", [])) < 2:
        raise ValueError("not the drivers' payroll table (headers missing)")
    cols["wage"], cols["commission"] = cols["money"][:2]
    cols["sales"] = cols["commission"] - 1
    cols["dept"] = cols["name"] + 1
    del cols["money"]
    for c in range(cols["net"] + 1, width):
        k = deduction_key(cell(1, c)) if len(header_rows) > 1 else None
        if cell(1, c).startswith("ค้าง") or cell(1, c).startswith("สะสมยก"):
            break
        if k and k not in ded:
            ded[k] = c
    cols["ded"] = ded
    return cols


def parse_xlsx_rows(values: list[list], formulas: list[list], month: str) -> list[dict]:
    """values / formulas: the sheet's rows from row 2 (header rows 2-4 first), each cut at the last deduction column.
    formulas may hold the formula strings (or values where the cell is typed)."""
    cols = header_columns(values[:3])
    out = []
    for i in range(3, len(values)):
        row, frow = values[i], formulas[i] if i < len(formulas) else []
        g = lambda k: row[cols[k]] if cols[k] < len(row) else None
        code = str(g("code") or "").strip().upper()
        if not CODE.match(code):
            continue
        days, wage, sales, comm = (_num(g(k)) for k in ("days", "wage", "sales", "commission"))
        bag_col, gross, dtot, net = (_num(g(k)) for k in ("bag", "gross", "deduct", "net"))
        if not any((days, gross, dtot, net)):
            continue
        name = re.sub(r"\s+", " ", str(g("name") or "")).strip()
        dept = re.sub(r"\s+", "", str(g("dept") or ""))          # "ขับรถหลอด (ใหญ่)" -> as the PDF prints it
        formula = frow[cols["bag"]] if cols["bag"] < len(frow) else None
        flags = []
        bonus = BONUS if bag_col and _bonus_from_formula(formula, bag_col) else 0.0
        typed = bag_col and not (isinstance(formula, str) and formula.startswith("=")) and abs(bag_col - BONUS) > 0.005
        if typed and (month, code) not in OVERRIDES:
            if bag_col > BIG_BAG:
                hint = " and the name says คืนสะสม" if "คืนสะสม" in name.replace(" ", "") else ""
                flags.append(f"ซอง% typed {bag_col:,.0f} (no formula){hint}: savings refund / back pay? counted as bag pay")
            elif bag_col > BONUS:      # a typed bag amount: no formula to show the 1,000, so the days decide (as in PDF months)
                bonus = BONUS if days >= period_days(month) else 0.0
                flags.append(f"ซอง% typed {bag_col:,.0f} (no formula): เบี้ยขยัน {bonus:,.0f} from the days")
        deds = {k: _num(row[c]) if c < len(row) else 0.0 for k, c in cols["ded"].items()}
        out.append(build(month, code, name, dept, days, wage, sales, comm, bag_col, gross, dtot, net, deds, "xlsx", bonus, flags,
                         all_deductions=True))
    return out


def _parse_xlsx(path: str, month: str) -> list[dict]:
    import openpyxl
    sheets = {}
    for data_only in (True, False):
        wb = openpyxl.load_workbook(path, data_only=data_only, read_only=True)
        name = next((n for n in wb.sheetnames if "รายรับ" in n), None)
        if name is None:
            raise ValueError("sheet ตารางรายรับ-จ่ายพนง. not found")
        ws = wb[name]
        head = [list(r) for r in ws.iter_rows(min_row=2, max_row=4, max_col=30, values_only=True)]
        cols = header_columns(head)
        last = max(cols["ded"].values(), default=cols["net"]) + 1        # stop before ค้างเงิน / สะสมยกไป / bank columns
        sheets[data_only] = [list(r) for r in ws.iter_rows(min_row=2, max_col=last, values_only=True)]
        wb.close()
    return parse_xlsx_rows(sheets[True], sheets[False], month)


# ---------------------------------------------------------------- PDF

def pdf_columns(header_words: list[tuple]) -> dict:
    """header_words: (x0, y0, x1, y1, text) above the first person. -> {field: right edge x}; numbers are right-aligned under
    their header, so a number belongs to the column whose header's right edge is nearest its own."""
    edges, money = {}, []
    joined = []
    for x0, y0, x1, y1, t in sorted(header_words, key=lambda w: (round(w[1]), w[0])):
        if joined and t[:1] in "ำา" and joined[-1][1] == round(y0) and x0 - joined[-1][3] < 3:
            joined[-1] = (joined[-1][0], joined[-1][1], joined[-1][2] + t, x1)
        else:
            joined.append((x0, round(y0), t, x1))
    for x0, _y, t, x1 in joined:
        t = re.sub(r"\s+", "", t)
        if t == "วัน":
            edges["days"] = x1
        elif t == "เงิน":
            money.append(x1)
        elif t == "ซอง%":
            edges["bag"] = x1
        elif t == "รวมรับ":
            edges["gross"] = x1
        elif t == "รวมจ่าย":
            edges["deduct"] = x1
        elif t == "คงเหลือ":
            edges["net"] = x1
        elif t.startswith("ค้าง") or t.startswith("จ่ายค้าง") or t.startswith("สะสมยก"):
            edges["skip:" + t] = x1
        else:
            k = deduction_key(t)
            if k and k not in edges:
                edges[k] = x1
    money.sort()
    if len(money) < 2 or not {"days", "bag", "gross", "deduct", "net"} <= edges.keys():
        raise ValueError("not the drivers' payroll summary (headers missing)")
    edges["wage"], edges["commission"] = money[:2]
    edges["sales"] = (money[0] + money[1]) / 2
    return edges


def parse_pdf_words(pages: list[list[tuple]], month: str) -> list[dict]:
    """pages: per page, the words (x0, y0, x1, y1, text) as pymupdf get_text("words") gives them (first 5 fields)."""
    out = []
    for words in pages:
        codes = sorted((w for w in words if CODE.match(w[4].strip().upper()) and w[4].strip().upper().startswith("SB-PD")),
                       key=lambda w: w[1])
        if not codes:
            continue
        top = codes[0][1]
        edges = pdf_columns([w for w in words if w[1] < top - 4])
        stop = min((w[1] for w in words if w[4].strip() == "รวม" and w[1] > codes[-1][1]), default=1e9)
        ded_keys = [k for k in DEDUCTION_KEYS if k in edges]
        for i, cw in enumerate(codes):
            cy = (cw[1] + cw[3]) / 2
            lo = -1e9 if i == 0 else ((codes[i - 1][1] + codes[i - 1][3]) / 2 + cy) / 2
            hi = stop if i == len(codes) - 1 else min(stop, (cy + (codes[i + 1][1] + codes[i + 1][3]) / 2) / 2)
            if i == 0:
                lo = top - 8
            band = [w for w in words if lo <= (w[1] + w[3]) / 2 < hi and w is not cw]
            vals, text = {}, []
            for x0, y0, x1, y1, t in band:
                t = t.strip()
                if x0 < edges["days"] - 30:               # row number, name, role
                    if x0 > cw[0] and not NUM.match(t):
                        text.append((x0, y0, t))
                    continue
                if t == "-" or not NUM.match(t):
                    if not NUM.match(t) and t != "-":
                        text.append((x0, y0, t))
                    continue
                col = min(edges, key=lambda k: abs(edges[k] - x1))
                if abs(edges[col] - x1) > 25 or col.startswith("skip:"):
                    continue
                vals[col] = vals.get(col, 0.0) + _num(t)
            out_row = _pdf_row(month, cw[4].strip().upper(), text, vals, ded_keys)
            if out_row:
                out.append(out_row)
    return out


def _pdf_row(month, code, text, vals, ded_keys):
    raw = fix_thai(" ".join(t for _x, _y, t in sorted(text, key=lambda w: w[0])))
    no_bonus = NO_BONUS in raw.replace(" ", "")
    raw = re.sub(r"-?\s*" + NO_BONUS, " ", raw.replace(" ", " "))
    m = ROLE.search(raw)
    name, dept = (raw[:m.start()], raw[m.start():]) if m else (raw, "")
    name, dept = re.sub(r"\s+", " ", name).strip(" -"), re.sub(r"\s+", "", dept)
    v = lambda k: vals.get(k, 0.0)
    days, gross, dtot, net = v("days"), v("gross"), v("deduct"), v("net")
    if not any((days, gross, dtot, net)):
        return None
    bag_col = v("bag")
    flags = []
    bonus = 0.0
    if no_bonus:
        flags.append("marked ไม่ให้เบี้ยขยัน")
    if bag_col >= BONUS - 0.005 and not no_bonus:
        if bag_col > BONUS + 0.005 and days < period_days(month):
            flags.append(f"เบี้ยขยัน guessed 0 from the PDF: ซอง% {bag_col:,.0f} but {days:g} days < {period_days(month)} in the month")
        else:
            bonus = BONUS
            flags.append("เบี้ยขยัน 1,000 guessed from the PDF (ซอง% >= 1,000)")
    deds = {k: v(k) for k in ded_keys}
    return build(month, code, name, dept, days, v("wage"), v("sales"), v("commission"), bag_col, gross, dtot, net, deds, "pdf",
                 bonus, flags, all_deductions=True)


def _parse_pdf(path: str, month: str) -> list[dict]:
    import pymupdf
    doc = pymupdf.open(path)
    try:
        pages = [[tuple(w[:5]) for w in pg.get_text("words")] for pg in doc]
    finally:
        doc.close()
    return parse_pdf_words(pages, month)


def parse_file(path: str, month: str) -> list[dict]:
    """month = "2026-MM". xlsx (preferred) or the summary PDF."""
    if not re.match(r"^\d{4}-\d{2}$", month):
        raise ValueError("month must be YYYY-MM")
    p = str(path).lower()
    if p.endswith((".xlsx", ".xlsm")):
        return _parse_xlsx(path, month)
    if p.endswith(".pdf"):
        return _parse_pdf(path, month)
    raise ValueError("payroll file must be .xlsx or .pdf")
