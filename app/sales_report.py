"""
The ice program's "รายงานสรุปยอดสินค้าต่อลูกค้ารายวัน" (daily sales per customer per product, PDF export) -> rows.

Text layout (pymupdf get_text, one value per line). Each page starts with a header (print date, page no., column titles); then, by
product (e.g. "ซอง"):
    <product name>
    dd/mm/yyyy                    a day
      <amount>  <customer>  "<qty> <unit>"  <weight kg>  <unit price>      one customer line, repeated
    "ผลรวม :"  <weight total>  <qty total>     the day's total (amount total is the line just before it)
A product may continue across pages without repeating its name. Weight-priced customers (CP: price 0.45 / kg) have a weight; the rest 0.

parse() is pure and unit-tested; the totals check catches a misread.
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


def parse(text: str) -> dict:
    """-> {"rows": [{date, product, customer, qty, unit, weight, price, amount}], "days": [{date, product, amount, qty}], "problems": [..]}"""
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("=====PAGE") and not _is_header(ln)]
    rows, days, problems = [], [], []
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
            i += 4
            continue
        if not NUM.match(ln) and not QTY.match(ln):
            product = ln                                    # a product heading
        else:
            problems.append(f"unread line near {day} {product}: {ln}")
        i += 1
    return {"rows": rows, "days": days, "problems": problems}
