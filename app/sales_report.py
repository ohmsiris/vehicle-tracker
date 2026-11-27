"""
The ice program's "รายงานสรุปยอดสินค้าต่อลูกค้ารายวัน" (daily sales per customer per product, PDF export) -> rows.

Text layout (pymupdf get_text, one value per line). Each page starts with a header (print date, page no., column titles); then, by
product (e.g. "ซอง"):
    <product name>
    dd/mm/yyyy                    a day
      <amount>  <customer>  "<qty> <unit>"  <weight kg>  <unit price>      one customer line, repeated
    "ผลรวม :"  <weight total>  <qty total>     the day's total (amount total is the line just before it)
A product may continue across pages without repeating its name. Weight-priced customers (CP: price 0.45 / kg) have a weight; the rest 0.

parse() is pure and unit-tested; the totals check catches a misread. It also returns each product's printed grand total ("totals") --
the drop box (app/inbox_r_sales.py, 24 Nov 2026) shows them as the file's own total. text_of() turns the PDF bytes into the text parse()
reads (the same extraction scripts/import_sales_program.py always used, so customer names -- and the books' keys -- do not change);
branch_in() and printed_on() read the report's footer and print date.
"""
import re

DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
NUM = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
QTY = re.compile(r"^(-?[\d,]+(?:\.\d+)?)\s+(\S.*)$")
GLUED = re.compile(r"^(.*\D)(-?\d[\d,]*\.\d+)\s+(\S+)$")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _is_header(ln: str) -> bool:
    if ln.startswith(("พิมพ์วันที", "หน้าที", "รายงานสรุปยอดสินค้า", "บริษัท สระบุรีวารีเทพ")):
        return True
    return ln in ("สินค้า / ลูกค้า", "ราคา/หน่วย", "ราคารวม", "จํานวน", "จำนวน", "หน่วย") or ln.startswith(("นํ", "น้ำหนัก")) and "Kg" in ln


# The program's PDF font draws the raised / shifted tone marks with glyphs that come out of the text layer as Cyrillic letters (U+0477 -
# U+047B) or as the Thai fonts' private-use forms, and สระอำ as nikhahit + สระอา. Owner, 28 Nov 2026: "ร้านเนืѸอ" on the sales list is ร้านเนื้อ.
TONES = "่้๊๋์"                        # ่ ้ ๊ ๋ ์
MARKS = {chr(0x0477 + i): m for i, m in enumerate(TONES)}
for _i, _m in enumerate(TONES):
    MARKS[chr(0xF705 + _i)] = MARKS[chr(0xF70A + _i)] = MARKS[chr(0xF713 + _i)] = _m
MARKS.update({"": "ั", "": "ิ", "": "ี", "": "ึ", "": "ื", "": "ํ", "": "็"})
NIKHAHIT_AA = re.compile("ํ([่-๋]?)า")


def fix_thai(text: str) -> str:
    """The Thai as it should read: tone marks back from the font's stand-ins, nikhahit + สระอา as สระอำ (นํѸา -> น้ำ, จํากัด -> จำกัด)."""
    if not text:
        return text
    out = "".join(MARKS.get(ch, ch) for ch in text)
    return NIKHAHIT_AA.sub(lambda m: m.group(1) + "ำ", out)


def text_of(data: bytes) -> str:
    """The PDF's text, page by page, with the Thai put right (fix_thai). Names stored before 28 Nov 2026 were fixed the same way, so the
    books' keys still match."""
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return fix_thai("\n=====PAGE\n".join(p.get_text() for p in doc))


FOOTER = re.compile(r"บริษัท\s*สระบุรีวารีเทพ\s*จ\S*กัด[ \t]*(?:\(\s*สาขา\s*([^)\n]+?)\s*\))?")
PRINTED = re.compile(r"พิมพ์วันที\S*\s*:\s*(\d{2})-(\d{2})-(\d{4})")


def branch_in(text: str):
    """The branch the report says it is from: its last line is the company name, with "(สาขาแก่งคอย)" after it for Kaeng Khoi and
    nothing for the head office (Saraburi). None when the footer is not there."""
    hits = FOOTER.findall(text or "")
    if not hits:
        return None
    named = [h.strip() for h in hits if h and h.strip()]
    return named[-1] if named else "สระบุรี"


def printed_on(text: str):
    """'พิมพ์วันที่ : 07-11-2026 21:43' -> '2026-11-07' (the report cannot hold a day after it was printed)."""
    m = PRINTED.search(text or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def parse(text: str) -> dict:
    """-> {"rows": [{date, product, customer, qty, unit, weight, price, amount}], "days": [{date, product, amount, qty}],
    "totals": [{product, amount, qty}] (the printed grand total of each product), "problems": [..]}"""
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("=====PAGE") and not _is_header(ln)]
    rows, days, totals, problems = [], [], [], []
    product, day = None, None
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = DATE.match(ln)
        if m:
            day = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            i += 1
            continue
        # a customer line: amount, name, "qty unit", weight, price
        if NUM.match(ln) and i + 4 < len(lines) and QTY.match(lines[i + 2]) and NUM.match(lines[i + 3]) and NUM.match(lines[i + 4]) \
                and not NUM.match(lines[i + 1]) and lines[i + 1] != "ผลรวม :":
            q = QTY.match(lines[i + 2])
            rows.append({"date": day, "product": product, "customer": lines[i + 1], "qty": _num(q.group(1)), "unit": q.group(2).strip(),
                         "weight": _num(lines[i + 3]), "price": _num(lines[i + 4]), "amount": _num(ln)})
            i += 5
            continue
        # a long name runs into the quantity on one line: amount, "name<qty> unit", weight, price
        joined = GLUED.match(lines[i + 1]) if NUM.match(ln) and i + 3 < len(lines) else None
        if joined and NUM.match(lines[i + 2]) and NUM.match(lines[i + 3]):
            rows.append({"date": day, "product": product, "customer": joined.group(1).strip(), "qty": _num(joined.group(2)),
                         "unit": joined.group(3), "weight": _num(lines[i + 2]), "price": _num(lines[i + 3]), "amount": _num(ln)})
            i += 4
            continue
        # the day's total: <amount total> "ผลรวม :" <weight total> <qty total>
        if NUM.match(ln) and i + 3 < len(lines) and lines[i + 1].startswith("ผลรวม"):
            total, qty = _num(ln), _num(lines[i + 3])
            got = [r for r in rows if r["date"] == day and r["product"] == product]
            s, sq = round(sum(r["amount"] for r in got), 2), round(sum(r["qty"] for r in got), 2)
            if abs(s - total) > 0.5 or abs(sq - qty) > 0.01:
                problems.append(f"{day} {product}: rows {s:,.2f} / {sq:g} vs total {total:,.2f} / {qty:g}")
            days.append({"date": day, "product": product, "amount": total, "qty": qty})
            i += 4
            continue
        # the product's grand total: <amount> <qty> "ผลรวมของ [ X ] :" <weight>
        if NUM.match(ln) and i + 3 < len(lines) and NUM.match(lines[i + 1]) and lines[i + 2].startswith("ผลรวมของ"):
            s = round(sum(d["amount"] for d in days if d["product"] == product), 2)
            if abs(s - _num(ln)) > 1:
                problems.append(f"{product}: days {s:,.2f} vs grand total {_num(ln):,.2f}")
            totals.append({"product": product, "amount": _num(ln), "qty": _num(lines[i + 1])})
            i += 4
            continue
        if not NUM.match(ln) and not QTY.match(ln):
            product = ln                                    # a product heading
        else:
            problems.append(f"unread line near {day} {product}: {ln}")
        i += 1
    return {"rows": rows, "days": days, "totals": totals, "problems": problems}
