"""
ใบงานคนขับ as the owner's own Word file (owner, 11 Nov 2026: "couldn't we just use the same format I sent as Word, and add rows?").

app/templates/route_sheet.docx is a copy of a real driver sheet (route 35). Nothing is redrawn: the header table, the customer
table (its column widths, TH Sarabun New 16, borders), the summary box and the section breaks are copied from it, and only the
text of each cell is replaced:

* blocks like the paper: a header + customers + รวมถุง / รวมเงิน; the first block has the summary box (รับ / คืน / เงิน / แบงก์)
  after it; every block starts on a new sheet (the template's section break). A block may run over more pages; Word flows it.
* a shop with a price per product: its ราคา is empty and each priced product cell gets the template's diagonal line with the
  price under it, exactly as the Word sheets do.
* with a day: quantities, money and the summary are filled in; without one it is the blank sheet, with empty rows at the end.
* several routes at once = one document, each route starting on a new sheet.

to_pdf() turns it into PDF with LibreOffice when the server has it (Dockerfile), using Laksaman for TH Sarabun New.
"""
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from app.route_sales import KEYS

TEMPLATE = Path(__file__).resolve().parent / "templates" / "route_sheet.docx"
FIRST_BLOCK, BLOCK = 17, 24          # customers per block, as the paper (block 1 leaves room for the summary box)
NAMES = {"หลอด": "ใหญ่", "โม่": "บด"}  # the owner's product names (10 Nov 2026)
MARK = "§"                       # an invisible marker in a diagonal cell (PDF only): LibreOffice does not draw cell diagonals


def _n(v) -> str:
    if v in (None, "", 0):
        return ""
    v = float(v)
    return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"


# --------------------------------------------------------------------------------------------- reading the template
def _rows(tbl: str) -> list[str]:
    return re.findall(r"<w:tr[ >].*?</w:tr>", tbl, re.S)


def _cells(tr: str) -> list[str]:
    return re.findall(r"<w:tc>.*?</w:tc>", tr, re.S)


def _split_row(tr: str) -> tuple[str, list[str], str]:
    """A row as (its start up to the first cell, the cells, '</w:tr>')."""
    first = tr.index("<w:tc>")
    return tr[:first], _cells(tr), "</w:tr>"


class _Template:
    def __init__(self, path: Path = TEMPLATE):
        with zipfile.ZipFile(path) as z:
            self.files = {n: z.read(n) for n in z.namelist()}
        doc = self.files["word/document.xml"].decode("utf-8")
        b = doc.index("<w:body>") + len("<w:body>")
        self.head, body = doc[:b], doc[b:]
        tables = re.findall(r"<w:tbl>.*?</w:tbl>", body, re.S)
        pos, i = [], 0
        for t in tables:
            j = body.index(t, i)
            pos.append((j, j + len(t)))
            i = j + len(t)
        self.hdr, cust, self.summary = tables[0], tables[1], tables[2]
        self.gap = body[pos[0][1]:pos[1][0]]                    # the tiny paragraph between two tables
        self.section = body[pos[2][1]:pos[3][0]]                # the paragraph that ends a block (section break: next sheet)
        self.tail = body[pos[-1][1]:]                           # last paragraph + the document's own section
        rows = _rows(cust)
        self.cust_start = cust[:cust.index(rows[0])]
        self.cust_head = rows[0]
        self.cust_row = rows[2]                                 # a one-price customer: the base of every customer row
        self.tot_bags, self.tot_money = rows[-2], rows[-1]


# --------------------------------------------------------------------------------------------- writing a cell
def _set(tc: str, paras: list[tuple[str, dict]], diag: Optional[bool] = None, valign: Optional[str] = None) -> str:
    """Replace a cell's text, keeping its width / borders / font. paras = [(text, {"b": bool, "sz": half-points, "jc": "left"})]."""
    m = re.search(r"<w:tcPr>.*?</w:tcPr>", tc, re.S)
    tcpr = m.group() if m else "<w:tcPr></w:tcPr>"
    p0 = re.search(r"<w:p[ >].*?</w:p>|<w:p/>", tc, re.S)
    ppr_m = re.search(r"<w:pPr>.*?</w:pPr>", p0.group() if p0 else "", re.S)
    ppr = ppr_m.group() if ppr_m else "<w:pPr></w:pPr>"
    rpr_m = re.search(r"<w:rPr>.*?</w:rPr>", ppr, re.S)
    rpr = rpr_m.group() if rpr_m else "<w:rPr></w:rPr>"
    if diag is not None:
        tcpr = re.sub(r"<w:tcBorders>.*?</w:tcBorders>", "", tcpr, flags=re.S)
        if diag:
            tcpr = re.sub(r"(<w:tcW [^>]*/>(?:<w:gridSpan [^>]*/>)?(?:<w:vMerge[^>]*/>)?)",
                          r'\1<w:tcBorders><w:tr2bl w:val="single" w:sz="6" w:space="0" w:color="auto"/></w:tcBorders>', tcpr, count=1)
    if valign:
        tcpr = re.sub(r'<w:vAlign w:val="\w+"/>', f'<w:vAlign w:val="{valign}"/>', tcpr) if "<w:vAlign" in tcpr \
            else tcpr.replace("</w:tcPr>", f'<w:vAlign w:val="{valign}"/></w:tcPr>')
    out = []
    for text, o in paras or [("", {})]:
        p, r = ppr, rpr
        if "jc" in o:
            p = re.sub(r'<w:jc w:val="\w+"/>', "", p)
            p = re.sub(r"(<w:pStyle [^>]*/>)", rf'\1<w:jc w:val="{o["jc"]}"/>', p, count=1) if "<w:pStyle" in p \
                else p.replace("<w:pPr>", f'<w:pPr><w:jc w:val="{o["jc"]}"/>', 1)
        if "b" in o:
            r = re.sub(r"<w:b/>|<w:bCs/>", "", r)
            if o["b"]:
                r = r.replace("</w:rPr>", "<w:b/><w:bCs/></w:rPr>") if "<w:sz" not in r else re.sub(r"(<w:sz )", r"<w:b/><w:bCs/>\1", r, count=1)
        if "sz" in o:
            r = re.sub(r'<w:sz w:val="\d+"/>', "", r)
            r = re.sub(r'<w:szCs w:val="\d+"/>', "", r)
            r = r.replace("</w:rPr>", f'<w:sz w:val="{o["sz"]}"/><w:szCs w:val="{o["sz"]}"/></w:rPr>')
        p = re.sub(r"<w:rPr>.*?</w:rPr>", r, p, flags=re.S) if "<w:rPr>" in p else p
        body = f'<w:r>{r}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>' if text else ""
        if o.get("mark"):
            body += f'<w:r><w:rPr><w:color w:val="FFFFFF"/><w:sz w:val="2"/><w:szCs w:val="2"/></w:rPr><w:t>{MARK}</w:t></w:r>'
        out.append(f"<w:p>{p}{body}</w:p>")
    return f"<w:tc>{tcpr}{''.join(out)}</w:tc>"


def _row(tr: str, values: dict) -> str:
    """values = {cell index: (paras, diag, valign)}; other cells emptied."""
    start, cells, end = _split_row(tr)
    out = []
    for i, c in enumerate(cells):
        paras, diag, valign = values.get(i, ([], None, None))
        out.append(_set(c, paras, diag, valign))
    return start + "".join(out) + end


def _rename(xml: str) -> str:
    return re.sub(r"(<w:t[^>]*>)([^<]*)(</w:t>)", lambda m: m.group(1) + NAMES.get(m.group(2).strip(), m.group(2)) + m.group(3), xml)


# --------------------------------------------------------------------------------------------- one route
def _header(t: _Template, s: dict, day_text: str) -> str:
    rows = _rows(t.hdr)
    keep = lambda r, i: _cells(r)[i]                                                   # noqa: E731
    r0 = _row(rows[0], _wrap({0: _text_of(keep(rows[0], 0)), 1: [(s["truck_no"], {})], 2: _text_of(keep(rows[0], 2)), 3: [(s["driver"], {})],
                              4: _text_of(keep(rows[0], 4)), 5: [(day_text, {})]}))
    r1 = _row(rows[1], _wrap({0: _text_of(keep(rows[1], 0)), 1: [(s["route"], {})], 2: _text_of(keep(rows[1], 2)), 3: [(s["phone"], {})]}))
    return t.hdr.replace(rows[0], r0, 1).replace(rows[1], r1, 1)


def _text_of(tc: str) -> list:
    return [("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", tc)), {})]


def _wrap(values: dict) -> dict:
    return {i: (v if isinstance(v, tuple) else (v, None, None)) for i, v in values.items()}


def _customer(t: _Template, row: Optional[dict], filled: bool = True, marks: bool = False) -> str:
    if row is None:                                                     # an empty row for a new shop
        return _row(t.cust_row, {})
    v = {0: [(row["name"], {"b": True})], 1: [(b, {}) for b in (row["bins"] or "").split(", ") if b] or [],
         2: [(row["price"], {})]}
    for i, k in enumerate(KEYS):
        q = _n(row["q"].get(k))
        if k in row.get("split", {}):
            paras = ([(q, {"jc": "left", "b": True, "sz": 28})] if q else []) + [(_n(row["split"][k]), {"jc": "right", "b": True, "sz": 18, "mark": marks})]
            v[3 + i] = (paras, True, "bottom")
        else:
            v[3 + i] = [(q, {})]
    money = [row["amount"], row["owed_before"], row["old_paid"], row["paid"], row["paid_all"]]
    for i, m in enumerate(money):
        v[9 + i] = [(_n(m), {"jc": "right"})]
    v[14] = [("วางบิล" if row["invoice"] and row["amount"] else _n(row["owed_after"]), {"jc": "right"})]
    if filled and not row["sold"]:                                       # a filled day: the shops sold to stand out
        v[0] = [(row["name"], {"b": False})]
    return _row(t.cust_row, _wrap(v))


def _totals(t: _Template, rows: list[dict], filled: bool) -> str:
    bags = {0: _text_of(_cells(t.tot_bags)[0])}
    money = {0: _text_of(_cells(t.tot_money)[0])}
    if filled:
        for i, k in enumerate(KEYS):
            bags[1 + i] = [(_n(sum(r["q"].get(k) or 0 for r in rows)), {})]
        for i, key in enumerate(("amount", "owed_before", "old_paid", "paid", "paid_all", "owed_after")):
            money[7 + i] = [(_n(sum(r.get(key) or 0 for r in rows if not (key == "owed_after" and r["invoice"]))), {"jc": "right"})]
    return _row(t.tot_bags, _wrap(bags)) + _row(t.tot_money, _wrap(money))


def _summary(t: _Template, sm: Optional[dict]) -> str:
    rows = _rows(t.summary)[:-1]                                         # no mileage / ตรวจ row (owner, 11 Nov 2026)
    out = []
    sm = sm or {}
    loads = sm.get("loads") or []
    trips = [loads[0] if len(loads) > 0 else {}, loads[1] if len(loads) > 1 else {},
             {k: sum((ld or {}).get(k) or 0 for ld in loads[2:]) or None for k in KEYS} if len(loads) > 2 else {}]
    stock = {1: trips[0], 2: trips[1], 3: trips[2], 4: sm.get("loaded") or {}, 5: sm.get("returned") or {}, 6: sm.get("sold") or {}}
    diff = sm.get("diff") or {}
    short = sum(-v for v in diff.values() if v and v < 0)
    over = sum(v for v in diff.values() if v and v > 0)
    col8 = {0: sm.get("amount"), 2: short or None, 3: over or None, 4: sm.get("owed_before"), 5: sm.get("owed_after")}
    col10 = {3: sm.get("old_paid"), 4: sm.get("expected_cash")}
    notes = sm.get("notes") or {}
    for ri, tr in enumerate(rows):
        cells = _cells(tr)
        v = {i: _text_of(c) for i, c in enumerate(cells)}                  # keep every label
        if ri in stock:
            for i, k in enumerate(KEYS):
                v[1 + i] = [(_n((stock[ri] or {}).get(k)), {})]
        if ri in col8 and len(cells) > 8:
            v[8] = [(_n(col8[ri]), {"jc": "right"})]
        if ri in col10 and len(cells) > 10:
            v[10] = [(_n(col10[ri]), {"jc": "right"})]
        if ri <= 4 and len(cells) > 12:
            v[12] = [(_n(notes.get(str([1000, 500, 100, 50, 20][ri]))), {"jc": "right"})]
        if ri == 5 and len(cells) > 11:
            v[11] = [(_n(sm.get("coins")), {"jc": "right"})]
        if ri == 6 and len(cells) > 10 and sm.get("counted") is not None:
            v[10] = [("รวมเงิน", {}), (_n(sm.get("counted")), {"jc": "right", "b": True})]
        if ri == 7 and len(cells) > 5:
            v[5] = [(_n(sum((sm.get("sold") or {}).get(k) or 0 for k in KEYS)), {"jc": "right"})]
        out.append(_row(tr, _wrap(v)))
    start = t.summary[:t.summary.index(_rows(t.summary)[0])]
    return _rename(start + "".join(out) + "</w:tbl>")


def _route_body(t: _Template, s: dict, marks: bool = False) -> list[str]:
    """One route as a list of blocks (each ends where the next sheet starts)."""
    filled = s.get("summary") is not None
    day_text = "/".join(reversed(s["day"].split("-"))) if s.get("day") else "...................."
    rows = list(s["rows"])
    blocks, i, first = [], 0, True
    while i < len(rows) or first:
        size = FIRST_BLOCK if first else BLOCK
        chunk = rows[i:i + size]
        i += size
        last = i >= len(rows)
        body_rows = [_customer(t, r, filled, marks) for r in chunk]
        if last:
            body_rows += [_customer(t, None)] * max(size - len(chunk), 4 if not first else 0)
        table = t.cust_start + _rename(t.cust_head) + "".join(body_rows) + _totals(t, chunk, filled) + "</w:tbl>"
        block = _header(t, s, day_text) + t.gap + table
        if first:
            block += t.gap + _summary(t, s.get("summary"))
        blocks.append(block)
        first = False
        if last:
            break
    return blocks


def build(sheets: list[dict], marks: bool = False) -> bytes:
    """One or more routes' sheets -> a .docx (bytes). marks: put the diagonal-cell markers in (for to_pdf)."""
    t = _Template()
    blocks = [b for s in sheets for b in _route_body(t, s, marks)]
    body = t.section.join(blocks) + t.tail
    doc = t.head + body
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in t.files.items():
            z.writestr(name, doc.encode("utf-8") if name == "word/document.xml" else data)
    return out.getvalue()


# --------------------------------------------------------------------------------------------- PDF (LibreOffice on the server)
FONTS_CONF = """<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>
<include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>{fonts}</dir>
<alias binding="same"><family>TH Sarabun New</family><prefer><family>Laksaman</family><family>Sarabun</family></prefer></alias>
<alias binding="same"><family>TH SarabunPSK</family><prefer><family>Laksaman</family><family>Sarabun</family></prefer></alias>
</fontconfig>"""


def soffice() -> Optional[str]:
    return shutil.which("soffice") or shutil.which("libreoffice")


def to_pdf(docx: bytes) -> bytes:
    """docx = build(..., marks=True)."""
    exe = soffice()
    if not exe:
        raise RuntimeError("no LibreOffice")
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "sheet.docx"
        src.write_bytes(docx)
        conf = Path(d) / "fonts.conf"
        conf.write_text(FONTS_CONF.format(fonts=Path(__file__).resolve().parent.parent / "static" / "fonts"), encoding="utf-8")
        env = {**os.environ, "FONTCONFIG_FILE": str(conf), "HOME": d}
        subprocess.run([exe, "--headless", "--norestore", f"-env:UserInstallation={(Path(d) / 'lo').as_uri()}", "--convert-to", "pdf", "--outdir", d, str(src)],
                       check=True, timeout=120, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return _draw_diagonals((Path(d) / "sheet.pdf").read_bytes())


def _draw_diagonals(pdf: bytes) -> bytes:
    """Each marker (a diagonal cell) -> the cell it sits in, from the table's ruling lines -> a line from its bottom-left to its
    top-right corner, like the Word sheet."""
    import pymupdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for page in doc:
        marks = [pymupdf.Rect(w[:4]) for w in page.get_text("words") if MARK in w[4]]
        if not marks:
            continue
        vert, horiz = [], []
        for d in page.get_drawings():
            for it in d["items"]:
                if it[0] == "l":
                    a, b = it[1], it[2]
                    if abs(a.x - b.x) < 1:
                        vert.append((a.x, min(a.y, b.y), max(a.y, b.y)))
                    elif abs(a.y - b.y) < 1:
                        horiz.append((a.y, min(a.x, b.x), max(a.x, b.x)))
                elif it[0] == "re":
                    r = it[1]
                    if r.width < 2 and r.height > 2:
                        vert.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
                    elif r.height < 2 and r.width > 2:
                        horiz.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
        for m in marks:
            cx, cy = (m.x0 + m.x1) / 2, (m.y0 + m.y1) / 2
            lefts = [x for x, y0, y1 in vert if x < cx and y0 - 1 <= cy <= y1 + 1]
            rights = [x for x, y0, y1 in vert if x > cx and y0 - 1 <= cy <= y1 + 1]
            tops = [y for y, x0, x1 in horiz if y < cy and x0 - 1 <= cx <= x1 + 1]
            bottoms = [y for y, x0, x1 in horiz if y > cy and x0 - 1 <= cx <= x1 + 1]
            if lefts and rights and tops and bottoms:
                page.draw_line((max(lefts), min(bottoms)), (min(rights), max(tops)), color=(0, 0, 0), width=0.6)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
