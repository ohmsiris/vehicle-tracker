"""
Per-person monthly pay for the Saraburi factory / office / mechanics group (codes SB-FR...), read from HQ's payroll file:
"เงินเดือน ช่าง+ออฟฟิศ+ฝ่ายผลิตสระบุรี <month> 2569 .xlsx" (sheet "ตารางรายรับ-จ่ายพนง.") or, for months HQ only sent a PDF, the
สรุป / ใบสรุป summary PDF of the same table. Never the slip sheets.

The table, left to right (column letters move between files, so everything is found from the "ซอง%" header):
  code (scan no. SB-FR...) · name · แผนก · วัน · เงิน (= วัน x rate) · [quantity, no header] · เงิน (= quantity x 40 OT, or x 1.5% sales
  commission) · ซอง% · รวมรับ · รวมจ่าย · คงเหลือ · deductions: สะสม, เบิกล่วงหน้า, เงินยืม, ส่วนกลาง, [ทำบัตร -- not in January],
  เงินขาด/ความผิด, อุปกรณ์/อื่นๆ, ปกส.
Nothing right of ปกส. is read: the ค้าง... columns, the bank account column and the cell comments (loan histories) stay untouched.

The PDF is parsed by position: the printed grid lines give the column and row boxes, every word goes to the box its centre falls in.

In this group the ซอง% column almost never holds bag pay: in 2569 it carried savings paid back ("-คืนสะสม" after the name), back
pay ("-รวมค่าแรงขาด เม.ย.-พ.ค.") and leaver settlements. Those amounts go to parts["other"] with a flag -- they only become "refund"
when the owner confirms them in OVERRIDES.

parse_file(path, month) -> list[dict] is the entry point; build_person(raw, month, source) holds the rules and is pure.
"""
import re
from typing import Optional

GROUP = "factory"
CODE_RE = re.compile(r"^SB-[A-Z]+\d+[A-Z]?$")
PART_KEYS = ["wage", "commission", "bag", "bonus", "ot", "other"]
DED_KEYS = ["savings", "advance", "loan", "common", "card", "shortage", "other", "sso"]

# Owner-confirmed money in the pay columns that is not pay for the month (taken out of ซอง% into "refund").
OVERRIDES = {("2026-09", "SB-PD025"): {"refund": 20500}}

# Notes HQ writes after a name that say the ซอง% / extra money is not this month's pay.
NOT_PAY_NOTES = ("คืนสะ", "ค่าแรงขาด", "จ่ายค้าง", "ลาออก", "คืนเงิน")
NO_BONUS_NOTE = "ไม่ให้เบี้ยขยัน"
COMMISSION_DEPTS = ("หน้าโรง", "แพ็ค", "พนักงานขาย", "ขับรถ", "พขร")
MARKS = "ัำิีึืฺุู็่้๊๋์ํ๎"


# ---------------------------------------------------------------------------------------------------------------- small helpers
def fix_thai(parts: list[str]) -> str:
    """Join PDF word fragments. A fragment starting with a vowel / tone mark belongs to the previous one ("วันท" "ำงาน"); one starting
    with sara-aa is a sara-am whose nikhahit was lost ("นางส" "าเนา" -> "นางสำเนา") -- no Thai word starts with า."""
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and p[0] in MARKS:
            out += p
        elif out and p[0] == "า" and ("ก" <= out[-1] <= "ฮ" or out[-1] in "่้๊๋"):
            out += "ำ" + p[1:]
        else:
            out += (" " if out else "") + p
    return re.sub(r"\s+", " ", out).strip()


def to_num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "–"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        x = float(s)
    except ValueError:
        return 0.0
    return -x if neg else x


def ded_key(label: str) -> Optional[str]:
    t = re.sub(r"\s+", "", label or "").lower()
    if "ปกส" in t or "ประกันสังคม" in t:
        return "sso"
    if "สะสม" in t and "ยกไป" not in t:
        return "savings"
    if "เบิก" in t:
        return "advance"
    if "ยืม" in t or "กู้" in t:
        return "loan"
    if "ส่วนกลาง" in t:
        return "common"
    if "บัตร" in t or "passport" in t:
        return "card"
    if "อุปกรณ์" in t or "อื่น" in t:
        return "other"
    if "ขาด" in t or "ผิด" in t or "กระสอบ" in t:
        return "shortage"
    return None


def layout(labels: dict[int, str]) -> dict:
    """labels: column index -> header text. Everything is placed relative to the ซอง% column."""
    s = next((c for c in sorted(labels) if "ซอง" in (labels[c] or "")), None)
    if s is None:
        raise ValueError("ไม่พบหัวคอลัมน์ ซอง%")
    lay = {"code": s - 7, "name": s - 6, "dept": s - 5, "days": s - 4, "wage": s - 3, "qty": s - 2, "money2": s - 1, "bag": s,
           "gross": s + 1, "deduct_total": s + 2, "net": s + 3}
    for k, word in (("gross", "รวมรับ"), ("deduct_total", "รวมจ่าย"), ("net", "คงเหลือ")):
        if word not in re.sub(r"\s+", "", labels.get(lay[k], "") or ""):
            raise ValueError(f"คอลัมน์ {word} ไม่อยู่ที่คาด")
    ded = {}
    c = s + 4
    while c in labels or c <= max(labels):
        k = ded_key(labels.get(c, ""))
        if k is None or k in ded:
            break
        ded[k] = c
        c += 1
        if k == "sso":
            break
    if "sso" not in ded:
        raise ValueError("ไม่พบคอลัมน์ ปกส.")
    lay["deductions"] = ded
    lay["last"] = ded["sso"]
    return lay


def dept_kind(dept: str) -> Optional[str]:
    d = dept or ""
    if "ผลิต" in d:
        return "ot"
    if any(k in d for k in COMMISSION_DEPTS):
        return "commission"
    return None


# ---------------------------------------------------------------------------------------------------------------- the rules
def build_person(raw: dict, month: str, source: str) -> dict:
    """raw: {code, name, dept, days, wage, money2, money2_formula, bag_cell, bag_formula, gross, gross_formula, deduct_total, net,
    deductions: {key: amount} (only the columns the file has), notes}. -> the per-person dict of the contract."""
    flags: list[str] = []
    code = re.sub(r"\s+", "", str(raw["code"])).upper()
    name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip()
    dept = re.sub(r"\s+", " ", str(raw.get("dept") or "")).strip()
    notes = " ".join([name, str(raw.get("notes") or "")])
    parts = {k: 0.0 for k in PART_KEYS}
    parts["wage"] = to_num(raw.get("wage"))

    # the second เงิน column: OT (quantity x 40) or sales commission (quantity x %)
    m2 = to_num(raw.get("money2"))
    if m2:
        f = raw.get("money2_formula")
        f = f if isinstance(f, str) and f.startswith("=") else None
        kind = None
        if f and ("%" in f or "/100" in f):
            kind = "commission"
        elif f and (mm := re.search(r"\*\s*(\d+(?:\.\d+)?)\s*$", f)):
            kind = "ot"
            if float(mm.group(1)) != 40:
                flags.append(f"เงิน {m2:,.0f}: สูตร {f} ถือเป็น OT (อัตรา {mm.group(1)})")
        else:
            kind = dept_kind(dept)
            how = "PDF" if source == "pdf" else "ไม่มีสูตร"
            if kind is None:
                kind = "ot"
                flags.append(f"เงิน {m2:,.0f}: แผนก '{dept}' ไม่รู้จัก ({how}) ถือเป็น OT")
            else:
                flags.append(f"เงิน {m2:,.0f}: {how} เดาจากแผนก '{dept}' = {'OT' if kind == 'ot' else 'ค่าคอม'}")
        parts[kind] += m2

    # ซอง%: bag pay + the 1,000 เบี้ยขยัน -- in this group mostly savings refunds / back pay (noted after the name)
    bagv = to_num(raw.get("bag_cell"))
    if bagv:
        f = raw.get("bag_formula")
        f = f if isinstance(f, str) and f.startswith("=") else None
        note = next((n for n in NOT_PAY_NOTES if n in notes), None)
        if note:
            parts["other"] += bagv
            flags.append(f"ซอง% {bagv:,.0f}: หมายเหตุ '{name}' -- น่าจะไม่ใช่ค่าจ้างเดือนนี้ (คืนสะสม/จ่ายค้าง) ใส่ไว้ใน other; ยืนยันแล้วเพิ่มใน OVERRIDES")
        else:
            bonus = 0.0
            if source == "xlsx":
                if f and (re.search(r"\+\s*1000(?:\.0+)?\s*$", f) or re.match(r"^=\s*1000(?:\.0+)?\s*\+", f)):
                    bonus = 1000.0
                elif abs(bagv - 1000) < 0.01:
                    bonus = 1000.0
                if not f and bagv != 1000:
                    flags.append(f"ซอง% {bagv:,.0f} พิมพ์ตัวเลข (ไม่มีสูตร) -- ตรวจว่าเป็นค่าซองจริงไหม")
            else:
                if bagv >= 1000 and NO_BONUS_NOTE not in notes:
                    bonus = 1000.0
                    flags.append("PDF: เบี้ยขยัน 1,000 เดาจาก ซอง% >= 1,000")
                if bagv - bonus:
                    flags.append(f"PDF: ซอง% {bagv:,.0f} -- ตรวจว่าเป็นค่าซองจริงไหม")
            parts["bonus"] += bonus
            parts["bag"] += bagv - bonus

    gross = to_num(raw.get("gross"))
    refund = 0.0
    ov = OVERRIDES.get((month, code))
    if ov and ov.get("refund"):
        refund = float(ov["refund"])
        left = refund
        for k in ("other", "bag", "bonus", "commission", "ot", "wage"):
            take = min(left, parts[k])
            parts[k] -= take
            left -= take
            if left <= 0:
                break
        flags.append(f"refund {refund:,.0f} (เจ้าของยืนยัน)")

    diff = round(gross - sum(parts.values()) - refund, 2)
    if abs(diff) >= 0.01:
        parts["other"] += diff
        flags.append(f"รวมรับ {gross:,.0f} ไม่เท่ากับ ค่าแรง+เงิน+ซอง% -- ส่วนต่าง {diff:,.2f} อยู่ใน other")

    deductions = {k: 0.0 for k in DED_KEYS}
    for k, v in (raw.get("deductions") or {}).items():
        deductions[k] += to_num(v)
    deduct_total = to_num(raw.get("deduct_total"))
    net = to_num(raw.get("net"))
    if abs(sum(deductions.values()) - deduct_total) > 1:
        flags.append(f"รายการหักรวม {sum(deductions.values()):,.0f} ไม่เท่ากับ รวมจ่าย {deduct_total:,.0f}")
    if abs(gross - deduct_total - net) > 1:
        flags.append(f"รวมรับ - รวมจ่าย ไม่เท่ากับ คงเหลือ ({gross:,.0f} - {deduct_total:,.0f} != {net:,.0f})")

    return {"month": month, "group": GROUP, "code": code, "name": name, "dept": dept, "days": to_num(raw.get("days")),
            "parts": {k: round(v, 2) for k, v in parts.items()}, "refund": refund, "gross": gross,
            "deductions": {k: round(v, 2) for k, v in deductions.items()}, "deduct_total": deduct_total, "net": net,
            "source": source, "flags": flags}


def _is_empty(raw: dict) -> bool:
    return not to_num(raw.get("gross")) and not to_num(raw.get("deduct_total")) and not to_num(raw.get("days")) \
        and not to_num(raw.get("net"))


# ---------------------------------------------------------------------------------------------------------------- xlsx
def _xlsx_rows(path: str) -> dict:
    import openpyxl
    wf = openpyxl.load_workbook(path, read_only=True, data_only=False)
    wv = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        name = "ตารางรายรับ-จ่ายพนง." if "ตารางรายรับ-จ่ายพนง." in wf.sheetnames else None
        if name is None:
            raise ValueError("ไม่พบชีต ตารางรายรับ-จ่ายพนง.")
        sf, sv = wf[name], wv[name]
        # header: rows above the first SB- code (looked for in the first 6 columns only)
        head, first = {}, None
        for i, row in enumerate(sf.iter_rows(min_row=1, max_row=12, max_col=6, values_only=True), start=1):
            if any(isinstance(v, str) and CODE_RE.match(v.strip().upper()) for v in row):
                first = i
                break
        if first is None:
            raise ValueError("ไม่พบรหัส SB- ในตาราง")
        for row in sf.iter_rows(min_row=1, max_row=first - 1, max_col=30, values_only=True):
            for c, v in enumerate(row, start=1):
                if isinstance(v, str) and v.strip():
                    head[c] = (head.get(c, "") + " " + v.strip()).strip()
        lay = layout(head)
        last = lay["last"]
        frows = list(sf.iter_rows(min_row=first, max_col=last, values_only=True))
        vrows = list(sv.iter_rows(min_row=first, max_col=last, values_only=True))
    finally:
        wf.close()
        wv.close()
    people, extras, total = [], [], None

    def cell(row, c):
        return row[c - 1] if c - 1 < len(row) else None

    for fr, vr in zip(frows, vrows):
        first_cell = cell(vr, 1)
        if isinstance(first_cell, str) and first_cell.strip().startswith("รวม"):
            total = {k: to_num(cell(vr, lay[k])) for k in ("gross", "deduct_total", "net")}
            break
        raw = {"code": cell(vr, lay["code"]), "name": cell(vr, lay["name"]), "dept": cell(vr, lay["dept"]),
               "days": cell(vr, lay["days"]), "wage": cell(vr, lay["wage"]), "money2": cell(vr, lay["money2"]),
               "money2_formula": cell(fr, lay["money2"]), "bag_cell": cell(vr, lay["bag"]), "bag_formula": cell(fr, lay["bag"]),
               "gross": cell(vr, lay["gross"]), "gross_formula": cell(fr, lay["gross"]), "deduct_total": cell(vr, lay["deduct_total"]),
               "net": cell(vr, lay["net"]), "deductions": {k: cell(vr, c) for k, c in lay["deductions"].items()}, "notes": ""}
        code = str(raw["code"] or "").strip().upper()
        if CODE_RE.match(code):
            people.append(raw)
        elif not _is_empty(raw):
            extras.append({"label": str(raw["name"] or raw["code"] or "").strip(), "gross": to_num(raw["gross"]),
                           "deduct_total": to_num(raw["deduct_total"]), "net": to_num(raw["net"])})
    return {"people": people, "extras": extras, "total": total, "has": set(lay["deductions"])}


# ---------------------------------------------------------------------------------------------------------------- pdf
def _cluster(vals, tol=1.2) -> list[float]:
    out: list[float] = []
    for v in sorted(vals):
        if out and v - out[-1] <= tol:
            continue
        out.append(v)
    return out


def _grid(page) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.x - p2.x) < 0.5 and abs(p1.y - p2.y) > 2:
                    xs.append(p1.x)
                elif abs(p1.y - p2.y) < 0.5 and abs(p1.x - p2.x) > 2:
                    ys.append(p1.y)
            elif it[0] == "re":
                r = it[1]
                if r.width < 1.5 and r.height > 2:
                    xs.append(r.x0)
                elif r.height < 1.5 and r.width > 2:
                    ys.append(r.y0)
    return _cluster(xs), _cluster(ys)


def _slot(v: float, edges: list[float]) -> Optional[int]:
    for i in range(len(edges) - 1):
        if edges[i] <= v < edges[i + 1]:
            return i
    return None


def _segments(page, xs: list[float]):
    """Words cut at the grid lines too: text that overflows its cell ("...AUNG)-ออกคืนสะสม" running into แผนก "ผลิต") is printed
    as one word, so words are rebuilt from characters and split where a character's centre crosses into the next column."""
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                cur, col, box = "", None, None
                for ch in span["chars"]:
                    x0, y0, x1, y1 = ch["bbox"]
                    c = _slot((x0 + x1) / 2, xs) if x1 > x0 else col
                    if ch["c"].isspace() or (c != col and cur):
                        if cur:
                            out.append((box, col, cur))
                        cur, box = "", None
                    if ch["c"].isspace():
                        continue
                    col = c
                    cur += ch["c"]
                    box = [x0, y0, x1, y1] if box is None else [min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1)]
                if cur:
                    out.append((box, col, cur))
    return out


def _pdf_rows(path: str) -> dict:
    import pymupdf
    people, extras, total, has = [], [], None, set()
    with pymupdf.open(path) as doc:
        for page in doc:
            xs, ys = _grid(page)
            if len(xs) < 10 or len(ys) < 3:
                continue
            cells: dict[tuple[int, int], list] = {}
            band_words: dict[int, list] = {}
            for (x0, y0, x1, y1), c, w in _segments(page, xs):
                b = _slot((y0 + y1) / 2, ys)
                if b is None:
                    continue
                band_words.setdefault(b, []).append(w)
                if c is not None:
                    cells.setdefault((b, c), []).append(((y0 + y1) / 2, x0, w))

            def text(b, c, cells=cells):
                items = sorted(cells.get((b, c), []), key=lambda t: t[0])
                lines: list[list] = []
                for it in items:                    # one printed line = centres within 4pt (Thai and Latin sit at different heights)
                    if lines and abs(it[0] - lines[-1][0][0]) <= 4:
                        lines[-1].append(it)
                    else:
                        lines.append([it])
                return fix_thai([w for ln in lines for _, _, w in sorted(ln, key=lambda t: t[1])])

            bands = sorted({b for b, _ in cells})
            code_band = [b for b in bands if any(CODE_RE.match(w.upper()) for _, _, w in
                                                 sum((cells.get((b, c), []) for c in range(len(xs))), []))]
            if not code_band:
                continue
            first = code_band[0]
            head = {}
            for b in bands:
                if b >= first:
                    break
                for c in range(len(xs) - 1):
                    t = text(b, c)
                    if t:
                        head[c] = (head.get(c, "") + " " + t).strip()
            lay = layout(head)
            has |= set(lay["deductions"])
            for b in bands:
                if b < first:
                    continue
                raw = {"code": text(b, lay["code"]), "name": text(b, lay["name"]), "dept": text(b, lay["dept"]),
                       "days": text(b, lay["days"]), "wage": text(b, lay["wage"]), "money2": text(b, lay["money2"]),
                       "money2_formula": None, "bag_cell": text(b, lay["bag"]), "bag_formula": None, "gross": text(b, lay["gross"]),
                       "deduct_total": text(b, lay["deduct_total"]), "net": text(b, lay["net"]),
                       "deductions": {k: text(b, c) for k, c in lay["deductions"].items()},
                       "notes": NO_BONUS_NOTE if NO_BONUS_NOTE in fix_thai(band_words.get(b, [])).replace(" ", "") else ""}
                code = raw["code"].replace(" ", "").upper()
                label = " ".join(t for t in (text(b, 0), raw["code"], raw["name"]) if t)
                if CODE_RE.match(code):
                    raw["code"] = code
                    people.append(raw)
                elif re.sub(r"[\s\d]", "", label) == "รวม":
                    total = {k: to_num(raw[k]) for k in ("gross", "deduct_total", "net")}
                    break
                elif not _is_empty(raw) and total is None:
                    extras.append({"label": raw["name"] or label, "gross": to_num(raw["gross"]),
                                   "deduct_total": to_num(raw["deduct_total"]), "net": to_num(raw["net"])})
    if not people:
        raise ValueError("ไม่พบแถวพนักงาน SB- ในไฟล์ PDF")
    return {"people": people, "extras": extras, "total": total, "has": has}


# ---------------------------------------------------------------------------------------------------------------- entry points
def parse_file_full(path: str, month: str) -> dict:
    """-> {"people": [contract dicts], "extras": [non-SB rows with money: petty-cash pay-backs], "total": the printed total row}."""
    low = path.lower()
    if low.endswith(".xlsx"):
        src, rows = "xlsx", _xlsx_rows(path)
    elif low.endswith(".pdf"):
        src, rows = "pdf", _pdf_rows(path)
    else:
        raise ValueError("รองรับเฉพาะ .xlsx / .pdf")
    people = []
    seen = set()
    for raw in rows["people"]:
        if _is_empty(raw):          # empty slot kept for someone who left: no days, no money
            continue
        p = build_person(raw, month, src)
        if p["code"] in seen:
            p["flags"].append("รหัสซ้ำในไฟล์")
        seen.add(p["code"])
        people.append(p)
    return {"people": people, "extras": rows["extras"], "total": rows["total"]}


def parse_file(path: str, month: str) -> list[dict]:
    return parse_file_full(path, month)["people"]
