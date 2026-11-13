"""Kaeng Khoi payroll, one row per person per month (the employee page's pay history).

Source: HQ's monthly file "เงินเดือนแก่งคอย <month> 2569.xlsx", sheet "สรุป" (the per-person main table, never the slip sheet), or for a
month with no workbook the printed summary PDF ("สรุป-เงินเดือนแก่งคอย ..." / "ใบสรุป..."), which is the same table printed from Excel.

The table (columns as in every 2569 workbook; found by their header labels, so a moved or hidden column does not matter):
  B Scan (code SB-KKxxx) · C รายชื่อ · D role (ตำแหน่ง/แผนก as printed: พขร. / เด็กรถ / ผลิต / ช่าง / เสมียน / ล้างกระสอบ)
  E วัน · F เงิน (= days x day rate) · G quantity (OT hours, or sales baht for SB-KK004) · H เงิน · I (the 1,000 เบี้ยขยัน column)
  J รวมรับ · K รวมจ่าย · L คงเหลือ
  M สะสม · N เบิกล่วงหน้า · O คืนเงินกู้ · P ส่วนกลาง · Q passport · R ความผิด · S อุปกรณ์/อื่นๆ · T ปกส.
Only columns A..T are ever read: the bank-account columns further right are never touched.

How H and I are split (see split_h / split_i):
  H  workbook formula "=G*2/100" (a % of sales) -> commission; "=G*40" / "=G*35" (hours x rate) -> OT; a typed amount -> commission
     (the drivers' trip money, also paid to the truck helpers, some of whom are listed as ผลิต -- flagged on those rows);
     a constant added to a % formula ("=G*2/100+280") -> that constant goes to other, flagged.
     PDF months have no formulas: H = G x 20..100 -> OT; H = G x p% -> commission; no quantity -> commission (as typed amounts are).
  I  1,000 -> เบี้ยขยัน (bonus); "=1000+70" -> bonus 1,000 + other 70 (flagged); "=7*40" -> OT; any other amount -> other, flagged
     (10,000 typed there in Feb/Mar/Jul equals the person's savings balance on the สะสม sheet: most likely savings paid back on
     leaving -- a refund only once the owner confirms it in OVERRIDES).
Rows with no SB- code (the petty-cash pay-back rows "ค่าใช้จ่าย 11-25/..", "ค่าน้ำมัน จ่ายสด11-25/..") are not people: parse_file skips
them, extra_rows returns them so a month can still be reconciled to the sheet total. Rows with no days and no money are skipped.
"""
import re

OVERRIDES = {("2026-09", "SB-PD025"): {"refund": 20500}}   # owner-confirmed: his savings paid back, not pay

PARTS = ("wage", "commission", "bag", "bonus", "ot", "other")
DEDUCTIONS = ("savings", "advance", "loan", "common", "card", "shortage", "other", "sso")
# deduction key -> header label as printed (search text)
DED_LABELS = (("savings", "สะสม"), ("advance", "เบิกล่วงหน้า"), ("loan", "คืนเงินกู้"), ("common", "ส่วนกลาง"), ("card", "passport"),
              ("shortage", "ความผิด"), ("other", "อุปกรณ์"), ("sso", "ปกส"))
DRIVER_ROLES = ("พขร", "เด็กรถ", "ขับรถ")
CODE = re.compile(r"(SB-[A-Za-z]{2}\d+)")
SARA = re.compile(r"\s+([\u0e31\u0e33-\u0e3a\u0e47-\u0e4e])")      # PDF text splits sara-am / tone marks off: "นายจ ำเริญ"
REMARK = re.compile(r"\s*-\s*(ออก.*)$")                              # "...-ออกคืนสะสม01/12/69", "นายอาจอ-ออก"


def _is_driver(dept: str) -> bool:
    return any(r in (dept or "") for r in DRIVER_ROLES)


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _thai(text) -> str:
    """Re-join what the PDF text layer splits: " ำ" / " ่" -> attached; " า" (sara-am printed as space + sara-aa, the nikhahit
    lost) -> "ำ" -- no Thai word starts with า."""
    t = re.sub(r"\s+", " ", str(text or ""))
    t = re.sub(r"(?<=[\u0e01-\u0e4e]) \u0e32", "\u0e33", t)
    return SARA.sub(r"\1", t).strip()


def _clean_name(name) -> tuple[str, list]:
    name = _thai(name)
    flags = []
    m = REMARK.search(name)
    if m:
        flags.append(f"หมายเหตุในชื่อ: {m.group(1)}")
        name = name[:m.start()].strip()
    return name, flags


def _terms(formula: str) -> list[str]:
    return [t for t in re.split(r"(?=[+])", formula.lstrip("=").replace(" ", "")) if t.strip("+")]


def _const(term: str):
    try:
        return float(term.strip("+"))
    except ValueError:
        return None


def split_h(value, formula=None, qty=None, dept="") -> tuple[dict, list]:
    """Workbook: the "เงิน" cell after the quantity column -> ({part: amount}, flags). formula: the cell's formula text, None when
    the amount is typed in. (PDF months have no formulas: split_h_pdf.)"""
    v = _num(value)
    if not v:
        return {}, []
    if formula is None:                                   # typed amount: driver trip money (truck helpers too)
        flags = [] if _is_driver(dept) else [f"H typed {v:,.0f} on a {dept or 'no-role'} row: treated as commission (truck share)"]
        return {"commission": v}, flags
    f = formula.replace(" ", "")
    if "%" in f or "/100" in f:
        consts = [c for c in (_const(t) for t in _terms(f)) if c is not None]
        extra = sum(consts)
        if extra:
            return {"commission": v - extra, "other": extra}, [f"H {f}: the +{extra:,.0f} on top of the % put in other"]
        return {"commission": v}, []
    if re.fullmatch(r"=[A-Z]+\d+\*\d+(\.\d+)?", f):
        rate = float(f.split("*")[1])
        if rate <= 100:
            return {"ot": v}, []
    return {"other": v}, [f"H formula {f} not recognised: put in other"]


def split_h_pdf(value, qty, dept="") -> tuple[dict, list]:
    """PDF month (no formulas): told apart by the arithmetic against the quantity column."""
    v, q = _num(value), _num(qty)
    if not v:
        return {}, []
    if not q:
        return split_h(v, None, None, dept)
    r = v / q
    if 20 <= r <= 100 and abs(v - q * round(r)) <= 0.51:
        return {"ot": v}, []
    for p in (1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5):
        if abs(v - q * p / 100) <= 1.01:
            return {"commission": v}, []
    if _is_driver(dept):
        return {"commission": v}, [f"PDF: H {v:,.0f} vs quantity {q:,.0f} not a known rate: commission by role"]
    return {"ot": v}, [f"PDF: H {v:,.0f} vs quantity {q:,.0f} not a known rate: OT by role ({dept})"]


def split_i(value, formula=None, dept="") -> tuple[dict, list]:
    """The 1,000 column (เบี้ยขยัน) -> ({part: amount}, flags)."""
    v = _num(value)
    if not v:
        return {}, []
    if formula:
        f = formula.replace(" ", "")
        terms = _terms(f)
        consts = [_const(t) for t in terms]
        if len(terms) >= 2 and all(c is not None for c in consts) and 1000 in consts:
            rest = v - 1000
            return {"bonus": 1000.0, "other": rest}, [f"I {f}: bonus 1,000 + {rest:,.0f} in other"]
        if re.fullmatch(r"=\d+(\.\d+)?\*\d+(\.\d+)?", f) and float(f.split("*")[1]) <= 100:
            return {"ot": v}, []
        if re.fullmatch(r"=\d+(\.\d+)?", f):
            return split_i(v, None, dept)
        return {"other": v}, [f"I formula {f} not recognised: put in other"]
    if abs(v - 1000) < 0.01:
        return {"bonus": 1000.0}, []
    if _is_driver(dept) and 1000 < v < 2000:
        return {"bonus": 1000.0, "other": v - 1000}, [f"I {v:,.0f}: bonus 1,000 + {v - 1000:,.0f} in other"]
    if v < 1000 and abs(v / 40 - round(v / 40)) < 1e-9:
        return {"ot": v}, [f"I {v:,.0f} typed (a multiple of 40): taken as OT hours x 40"]
    return {"other": v}, [f"I {v:,.0f} is not the 1,000 bonus: put in other (savings paid back? needs an OVERRIDES entry to be a refund)"]


def build_row(month, source, code, name, dept, days, wage, h_parts, i_parts, gross, deds, deduct_total, net, flags=None,
              all_deductions=True) -> dict:
    """Assemble one person-month and check the sheet's own arithmetic; anything that does not add up is flagged."""
    flags = list(flags or [])
    code = str(code).strip().upper()
    parts = {k: 0.0 for k in PARTS}
    parts["wage"] = _num(wage)
    for d in (h_parts, i_parts):
        for k, v in d.items():
            parts[k] += v
    gross, deduct_total, net = _num(gross), _num(deduct_total), _num(net)
    refund = 0.0
    ov = OVERRIDES.get((month, code))
    if ov and ov.get("refund"):
        refund = float(ov["refund"])
        left = refund
        for k in ("other", "bag", "bonus", "commission", "ot", "wage"):
            take = min(left, parts[k])
            parts[k] -= take
            left -= take
        flags.append(f"owner-confirmed refund {refund:,.0f} taken out of pay")
    diff = gross - sum(parts.values()) - refund
    if abs(diff) > 1:
        parts["other"] += diff
        flags.append(f"pay columns differ from รวมรับ by {diff:,.0f}: difference put in other")
    dd = {k: _num(deds.get(k)) for k in DEDUCTIONS}
    if all_deductions and abs(sum(dd.values()) - deduct_total) > 1:
        flags.append(f"deductions add to {sum(dd.values()):,.0f}, รวมจ่าย is {deduct_total:,.0f}")
    if abs(gross - deduct_total - net) > 1:
        flags.append(f"รวมรับ - รวมจ่าย = {gross - deduct_total:,.0f}, คงเหลือ is {net:,.0f}")
    return {"month": month, "group": "kk", "code": code, "name": name, "dept": dept or "", "days": _num(days),
            "parts": {k: round(v, 2) for k, v in parts.items()}, "refund": refund, "gross": gross,
            "deductions": {k: round(v, 2) for k, v in dd.items()}, "deduct_total": deduct_total, "net": net,
            "source": source, "flags": flags}


# ---------------------------------------------------------------- workbook

def _xlsx_layout(ws_f) -> dict:
    """Column numbers (1-based) by header label, from the header rows (only columns A..X are scanned)."""
    col = {}
    for row in ws_f.iter_rows(min_row=1, max_row=6, max_col=24):
        for c in row:
            t = str(c.value).strip() if isinstance(c.value, str) else ""
            if not t:
                continue
            if t.lower() == "scan" or t.lower().startswith("scan"):
                col.setdefault("code", c.column)
            elif t == "รายชื่อ":
                col.setdefault("name", c.column)
            elif t == "รวมรับ":
                col.setdefault("gross", c.column)
            elif t == "รวมจ่าย":
                col.setdefault("deduct_total", c.column)
            elif t == "คงเหลือ":
                col.setdefault("net", c.column)
            else:
                for key, lab in DED_LABELS:
                    if t.startswith(lab) and not t.startswith("สะสมยกไป"):
                        col.setdefault("d_" + key, c.column)
    for k in ("code", "name", "gross", "deduct_total", "net"):
        if k not in col:
            raise ValueError(f"header '{k}' not found in sheet สรุป")
    j = col["gross"]
    col.update(dept=col["name"] + 1, days=j - 5, wage=j - 4, qty=j - 3, h=j - 2, i=j - 1)
    return col


def parse_xlsx(path: str, month: str) -> list[dict]:
    import openpyxl
    wf = openpyxl.load_workbook(path, read_only=True)               # formulas
    wv = openpyxl.load_workbook(path, read_only=True, data_only=True)  # cached values
    sf = wf["สรุป"] if "สรุป" in wf.sheetnames else wf.worksheets[0]
    sv = wv[sf.title]
    col = _xlsx_layout(sf)
    maxc = max(v for v in col.values())
    all_deds = all("d_" + k in col for k, _ in DED_LABELS)
    out = []
    rows_f = sf.iter_rows(min_row=5, max_row=sf.max_row, max_col=maxc, values_only=True)
    rows_v = sv.iter_rows(min_row=5, max_row=sv.max_row, max_col=maxc, values_only=True)
    for rf, rv in zip(rows_f, rows_v):
        if isinstance(rf[0], str) and rf[0].strip() == "รวม":
            break
        code = rv[col["code"] - 1]
        m = CODE.match(str(code or "").strip())
        if not m:
            continue
        g = lambda k: rv[col[k] - 1]
        fo = lambda k: rf[col[k] - 1] if isinstance(rf[col[k] - 1], str) and str(rf[col[k] - 1]).startswith("=") else None
        gross, ded, net, days = _num(g("gross")), _num(g("deduct_total")), _num(g("net")), _num(g("days"))
        if not (gross or ded or net or days):
            continue
        dept = str(g("dept") or "").strip()
        name, flags = _clean_name(g("name"))
        hp, hf = split_h(g("h"), fo("h"), g("qty"), dept)
        ip, if_ = split_i(g("i"), fo("i"), dept)
        deds = {k: (rv[col["d_" + k] - 1] if "d_" + k in col else 0) for k in DEDUCTIONS}
        out.append(build_row(month, "xlsx", m.group(1).upper(), name, dept, days, g("wage"), hp, ip, gross, deds, ded, net,
                             flags + hf + if_, all_deds))
    return out


def _xlsx_extra(path: str) -> list[dict]:
    import openpyxl
    wv = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sv = wv["สรุป"] if "สรุป" in wv.sheetnames else wv.worksheets[0]
    wf = openpyxl.load_workbook(path, read_only=True)
    col = _xlsx_layout(wf[sv.title])
    out = []
    for rv in sv.iter_rows(min_row=5, max_row=sv.max_row, max_col=col["net"], values_only=True):
        if isinstance(rv[0], str) and rv[0].strip() == "รวม":
            break
        if CODE.match(str(rv[col["code"] - 1] or "").strip()):
            continue
        gr, de, ne = (_num(rv[col[k] - 1]) for k in ("gross", "deduct_total", "net"))
        if gr or de or ne:
            out.append({"label": str(rv[col["name"] - 1] or "").strip(), "gross": gr, "deduct_total": de, "net": ne})
    return out


# ---------------------------------------------------------------- PDF

def _cluster(vals, tol=1.5) -> list[float]:
    out = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _grid(page) -> tuple[list, list]:
    xs, ys = set(), set()
    for dr in page.get_drawings():
        for it in dr["items"]:
            if it[0] == "l":
                p, q = it[1], it[2]
                if abs(p.x - q.x) < 0.5 and abs(p.y - q.y) > 3:
                    xs.add(p.x)
                elif abs(p.y - q.y) < 0.5 and abs(p.x - q.x) > 3:
                    ys.add(p.y)
            elif it[0] == "re":
                r = it[1]
                if r.width < 1.5 and r.height > 3:
                    xs.add(r.x0)
                if r.height < 1.5 and r.width > 3:
                    ys.add(r.y0)
    return _cluster(xs), _cluster(ys)


def _band(x, xs):
    for k in range(len(xs) - 1):
        if xs[k] <= x < xs[k + 1]:
            return k
    return None


def _words(page, xs) -> list[tuple]:
    """(x0, y0, x1, y1, text) tokens from the characters, cut at spaces AND at the column lines: get_text("words") glues two
    neighbouring cells into one word ("เพชรจันทร์พขร.") when no space separates them."""
    out = []
    for blk in page.get_text("rawdict")["blocks"]:
        for line in blk.get("lines", []):
            for span in line["spans"]:
                cur, band = [], None
                for ch in span["chars"]:
                    c, (x0, y0, x1, y1) = ch["c"], ch["bbox"]
                    if c.isspace():
                        if cur:
                            out.append(cur)
                        cur, band = [], None
                        continue
                    b = band if (cur and x1 - x0 < 0.5) else _band((x0 + x1) / 2, xs)   # zero-width marks stay with their letter
                    if cur and b != band:
                        out.append(cur)
                        cur = []
                    cur.append((x0, y0, x1, y1, c))
                    band = b
                if cur:
                    out.append(cur)
    return [(min(c[0] for c in t), min(c[1] for c in t), max(c[2] for c in t), max(c[3] for c in t), "".join(c[4] for c in t))
            for t in out]


def _pdf_layout(page, xs, header_bottom) -> dict:
    import pymupdf
    clip = pymupdf.Rect(0, 0, page.rect.width, header_bottom)

    def find(label, after=None):
        hits = sorted(((r.x0 + r.x1) / 2 for r in page.search_for(label, clip=clip)))
        bands = [b for b in (_band(x, xs) for x in hits) if b is not None and (after is None or b > after)]
        return bands[0] if bands else None
    col = {"code": find("Scan"), "name": find("รายชื่อ"), "gross": find("รวมรับ"), "deduct_total": find("รวมจ่าย"),
           "net": find("คงเหลือ")}
    if None in col.values():
        if len(xs) == 25:          # the standard A..X grid: fall back on position
            col = {"code": 1, "name": 2, "gross": 9, "deduct_total": 10, "net": 11}
        else:
            raise ValueError(f"PDF header not found: {col}")
    for key, lab in DED_LABELS:
        b = find(lab, after=col["net"])
        if b is not None:
            col["d_" + key] = b
    j = col["gross"]
    col.update(dept=col["name"] + 1, days=j - 5, wage=j - 4, qty=j - 3, h=j - 2, i=j - 1)
    return col


def _pdf_rows(path):
    """-> (rows, all_deductions): each row a {colkey: text} dict for one table row of every page."""
    import pymupdf
    doc = pymupdf.open(path)
    rows, all_deds = [], True
    for page in doc:
        xs, ys = _grid(page)
        words = _words(page, xs)
        bands = list(zip(ys, ys[1:]))
        first = None
        for y0, y1 in bands:
            if any(y0 <= (w[1] + w[3]) / 2 < y1 and CODE.match(w[4]) for w in words):
                first = y0
                break
        if first is None:
            continue
        col = _pdf_layout(page, xs, first)
        all_deds = all_deds and all("d_" + k in col for k, _ in DED_LABELS)
        inv = {}
        for k, b in col.items():
            inv.setdefault(b, k)
        for y0, y1 in bands:
            if y0 < first - 0.5:
                continue
            cells = {}
            for w in sorted((w for w in words if y0 <= (w[1] + w[3]) / 2 < y1), key=lambda w: (round(w[1]), w[0])):
                b = _band((w[0] + w[2]) / 2, xs)
                if b is not None and b in inv:
                    cells.setdefault(inv[b], []).append(w[4])
            if cells:
                rows.append({k: " ".join(v) for k, v in cells.items()})
    return rows, all_deds


def parse_pdf(path: str, month: str) -> list[dict]:
    rows, all_deds = _pdf_rows(path)
    out = []
    for c in rows:
        m = CODE.search(c.get("code", ""))
        if not m:
            continue
        g = lambda k: _num(c.get(k))
        gross, ded, net, days = g("gross"), g("deduct_total"), g("net"), g("days")
        if not (gross or ded or net or days):
            continue
        dept = SARA.sub(r"\1", c.get("dept", "")).strip()
        name, flags = _clean_name(c.get("name"))
        hp, hf = split_h_pdf(c.get("h"), c.get("qty"), dept)
        ip, if_ = split_i(c.get("i"), None, dept)
        deds = {k: c.get("d_" + k) for k in DEDUCTIONS}
        out.append(build_row(month, "pdf", m.group(1).upper(), name, dept, days, c.get("wage"), hp, ip, gross, deds, ded, net,
                             flags + hf + if_, all_deds))
    return out


def _pdf_extra(path: str) -> list[dict]:
    rows, _ = _pdf_rows(path)
    out = []
    for c in rows:
        if CODE.search(c.get("code", "")):
            continue
        label = SARA.sub(r"\1", c.get("name", "")).strip()
        if not label or label.startswith("รวม"):
            continue
        gr, de, ne = (_num(c.get(k)) for k in ("gross", "deduct_total", "net"))
        if gr or de or ne:
            out.append({"label": label, "gross": gr, "deduct_total": de, "net": ne})
    return out


# ---------------------------------------------------------------- entry points

def parse_file(path: str, month: str) -> list[dict]:
    """One Kaeng Khoi payroll file (xlsx main sheet สรุป, or the สรุป/ใบสรุป PDF) -> one dict per person with an SB- code."""
    return parse_pdf(path, month) if str(path).lower().endswith(".pdf") else parse_xlsx(path, month)


def extra_rows(path: str) -> list[dict]:
    """The sheet's rows that are not people (petty-cash / cash-fuel pay-back): [{label, gross, deduct_total, net}].
    Person rows + these = the sheet's total row (and HQ's monthly total)."""
    return _pdf_extra(path) if str(path).lower().endswith(".pdf") else _xlsx_extra(path)
