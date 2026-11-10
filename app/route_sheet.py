"""
ใบงานคนขับ: the driver's paper sheet (the Word files in `7 ใบขาย คนขับ`), made from IceBox -- owner, 10 Nov 2026.

The bookkeeper prints it straight away, as Excel (to_xlsx) or from the print view in the office page (static/salesheets.js, saved as
PDF from the browser). Same layout as the paper: the header (รถเบอร์ / พนักงานขับรถ / วันที่ / สาย / เบอร์โทรศัพท์), the customers in
route order with ถัง and ราคา, one column per product, รวมเงิน, เก่ายกมา, จ่ายเก่า, จ่ายสด, รวมจ่าย, ค้าง, then รวมถุง / รวมเงิน and the
box underneath: รับ by trip, คืน, ขายได้, the money, the notes counted, and the mileage. Black and white (official paper).

* With a day the phone sheet holds, every number is filled in. Without one it is the blank sheet for the driver to fill by hand.
* เก่ายกมา = what the customer owed before this day (all earlier days in IceBox); ค้าง = what they owe after it.
* Shops a driver added (เพิ่มลูกค้าเอง) come after the route's customers, marked "(เพิ่มเอง)".
"""
from datetime import date
from io import BytesIO
from typing import Optional

from app.route_sales import KEYS, NOTES, PRODUCTS, SHORT, _num, clean_walkins

COLS = ["ลูกค้า", "ถัง", "ราคา", *[lab for _, lab in PRODUCTS], "รวมเงิน", "เก่ายกมา", "จ่ายเก่า", "จ่ายสด", "รวมจ่าย", "ค้าง"]


def price_text(prices: dict, free: bool = False) -> str:
    """ราคา as the paper writes it: one number when every product costs the same, else one letter per product (ญ40 พ90)."""
    if free:
        return "ฟรี"
    p = {k: _num(v) for k, v in (prices or {}).items() if k in KEYS and _num(v) > 0}
    if not p:
        return ""
    vals = set(p.values())
    if len(vals) == 1:
        return f"{next(iter(vals)):g}"
    return " ".join(f"{SHORT[k]}{p[k]:g}" for k in KEYS if k in p)


def build(route: dict, customers: list[dict], day: Optional[str], day_row: Optional[dict], lines: list[dict], before: dict) -> dict:
    """Everything one printed sheet shows. customers = the route list (seq order); day_row = sale_days (None: a blank sheet);
    lines = that day's sale_lines; before = {customer id (str): owed before this day}."""
    by_cust = {str(ln["customer_id"]): ln for ln in lines if ln.get("customer_id")}
    by_walk = {ln["walkin"]: ln for ln in lines if ln.get("walkin")}
    rows = []

    def row(name, bins, prices, free, ln, owed):
        q = (ln or {}).get("qty") or {}
        amount = _num((ln or {}).get("amount"))
        paid, old = _num((ln or {}).get("paid")), _num((ln or {}).get("old_paid"))
        invoice = (ln or {}).get("billing") == "invoice"
        after = owed if invoice else owed + amount - paid - old
        return {"name": name, "bins": bins or "", "price": price_text(prices, free), "q": {k: _num(q.get(k)) or None for k in KEYS},
                "amount": amount or None, "owed_before": owed or None, "old_paid": old or None, "paid": paid or None,
                "paid_all": (paid + old) or None, "owed_after": round(after, 2) or None, "invoice": invoice, "sold": bool(ln)}

    for c in customers:
        rows.append({"seq": c["seq"], **row(f"{c['seq']}. {c['name']}", c.get("bins"), c.get("prices"), c.get("free"),
                                             by_cust.get(str(c["id"])), _num(before.get(str(c["id"]))))})
    doc = (day_row or {}).get("doc") or {}
    for w in clean_walkins(doc.get("new_customers")):
        rows.append({"seq": None, **row(f"{w['name']} (เพิ่มเอง)", "", w["prices"], False, by_walk.get(w["name"]), 0.0)})

    t = (day_row or {}).get("totals") or {}
    loads = [{k: _num((ld.get("q") or {}).get(k)) or None for k in KEYS} for ld in doc.get("loads") or []]
    cash = doc.get("cash") or {}
    totals = {
        "q": {k: sum(r["q"][k] or 0 for r in rows) or None for k in KEYS},
        "amount": sum(r["amount"] or 0 for r in rows) or None, "owed_before": sum(r["owed_before"] or 0 for r in rows) or None,
        "old_paid": sum(r["old_paid"] or 0 for r in rows) or None, "paid": sum(r["paid"] or 0 for r in rows) or None,
        "paid_all": sum(r["paid_all"] or 0 for r in rows) or None, "owed_after": sum(r["owed_after"] or 0 for r in rows) or None,
    }
    returned = (day_row or {}).get("returns_foh")
    summary = None
    if day_row:
        km_start, km = t.get("km_start"), t.get("km")
        summary = {
            "loads": loads,
            "loaded": {k: _num((t.get("loaded") or {}).get(k)) or None for k in KEYS},
            "returned": {k: _num((returned or {}).get(k)) or None for k in KEYS} if returned else None,
            "sold": {k: _num((t.get("sold") or {}).get(k)) or None for k in KEYS},
            "diff": {k: _num((t.get("diff") or {}).get(k)) or None for k in KEYS} if returned else None,
            "amount": t.get("amount"), "invoiced": t.get("invoiced"), "credit": t.get("credit"), "cash_sales": t.get("cash_sales"),
            "old_paid": t.get("old_paid"), "expected_cash": t.get("expected_cash"), "counted": t.get("counted"),
            "owed_before": totals["owed_before"], "owed_after": totals["owed_after"],
            "notes": {str(n): _num(cash.get(str(n))) or None for n in NOTES}, "coins": _num(cash.get("coins")) or None,
            "km_end": _num(doc.get("km_end")) or None, "km_start": km_start, "km": km, "flags": t.get("flags") or [],
            "status": day_row.get("status"),
        }
    return {"truck_no": route["truck_no"], "driver": route.get("driver_name") or "", "route": route.get("name") or "",
            "phone": route.get("driver_phone") or "", "day": day, "products": [{"key": k, "label": lab} for k, lab in PRODUCTS],
            "rows": rows, "totals": totals, "summary": summary}


def load(route_id: int, day: Optional[date]) -> dict:
    from app import supabase
    from app.route_sales import RouteSalesError, _customers, _day
    r = supabase.select("sale_routes", {"select": "id,truck_no,name,driver_name,driver_phone", "id": f"eq.{int(route_id)}", "limit": "1"})
    if not r:
        raise RouteSalesError("ไม่พบสายนี้")
    d = _day(r[0]["id"], day) if day else None
    lines, before = [], {}
    if d:
        lines = supabase.select_all("sale_lines", {"select": "customer_id,walkin,qty,amount,paid,old_paid,billing", "day_id": f"eq.{d['id']}",
                                                   "order": "id.asc"})
        for ln in supabase.select_all("sale_lines", {"select": "customer_id,amount,paid,old_paid,billing", "route_id": f"eq.{r[0]['id']}",
                                                     "day": f"lt.{day.isoformat()}", "order": "id.asc"}):
            if ln["customer_id"] and ln["billing"] != "invoice":          # as route_sales._balances: วางบิล is not ค้าง
                k = str(ln["customer_id"])
                before[k] = before.get(k, 0.0) + _num(ln["amount"]) - _num(ln["paid"]) - _num(ln["old_paid"])
    return build(r[0], _customers(r[0]["id"]), day.isoformat() if day else None, d, lines, before)


def be_date(iso: Optional[str]) -> str:
    if not iso:
        return ""
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


# --------------------------------------------------------------------------------------------- Excel
def to_xlsx(s: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = f"รถ {s['truck_no']}"
    thin = Side(style="thin", color="000000")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    F = "Tahoma"
    f_n, f_b, f_h = Font(name=F, size=9), Font(name=F, size=9, bold=True), Font(name=F, size=12, bold=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right = Alignment(horizontal="right", vertical="center")
    widths = [24, 10, 7, 5.5, 5.5, 5.5, 5.5, 5.5, 5.5, 9, 8, 8, 8, 9, 8]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ncol = len(COLS)

    def put(r, c, v, font=f_n, align=left, border=True, fmt=None):
        cell = ws.cell(row=r, column=c, value=v)
        cell.font, cell.alignment = font, align
        if border:
            cell.border = box
        if fmt:
            cell.number_format = fmt
        return cell

    # header (rows 1-2), repeated on every printed page with the column titles (row 3)
    one = Alignment(horizontal="left", vertical="center", wrap_text=False)
    for rr, cells in ((1, [(1, 3, f"รถเบอร์  {s['truck_no']}", f_h), (4, 5, "พนักงานขับรถ", f_n), (6, 10, s["driver"], f_h),
                           (11, 12, "วันที่", f_n), (13, 15, be_date(s["day"]) or "....................", f_h)]),
                      (2, [(1, 3, f"สาย  {s['route']}", f_h), (4, 5, "เบอร์โทรศัพท์", f_n), (6, 10, s["phone"], f_h)])):
        for c1, c2, v, font in cells:
            ws.merge_cells(start_row=rr, start_column=c1, end_row=rr, end_column=c2)
            put(rr, c1, v, font, one, False)
        ws.row_dimensions[rr].height = 22
    for c, title in enumerate(COLS, start=1):
        put(3, c, title, f_b, center)
    ws.row_dimensions[3].height = 30
    MONEY, QTY = "#,##0.##;-#,##0.##;", "#,##0.##;-#,##0.##;"
    r = 4
    for row in s["rows"]:
        put(r, 1, row["name"], f_b if row["sold"] else f_n)
        put(r, 2, row["bins"].replace(", ", "\n"), Font(name=F, size=7), center)
        put(r, 3, row["price"], f_n, center)
        for i, k in enumerate(KEYS):
            put(r, 4 + i, row["q"][k], f_n, center, fmt=QTY)
        put(r, 10, row["amount"], f_n, right, fmt=MONEY)
        for c, key in ((11, "owed_before"), (12, "old_paid"), (13, "paid"), (14, "paid_all")):
            put(r, c, row[key], f_n, right, fmt=MONEY)
        if not (row["invoice"] and row["amount"]):
            put(r, 15, row["owed_after"], f_n, right, fmt=MONEY)
        else:
            put(r, 15, "วางบิล", f_n, center)
        nb = max(1, len([b for b in row["bins"].split(", ") if b]))
        ws.row_dimensions[r].height = max(24, 10 * nb + 4)
        r += 1
    tt = s["totals"]
    for label, vals in (("รวมถุง", None), ("รวมเงิน", tt)):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        put(r, 1, label, f_b, center)
        for c in (2, 3):
            ws.cell(row=r, column=c).border = box
        for i, k in enumerate(KEYS):
            put(r, 4 + i, tt["q"][k] if vals is None else None, f_b, center, fmt=QTY)
        for c, key in ((10, "amount"), (11, "owed_before"), (12, "old_paid"), (13, "paid"), (14, "paid_all"), (15, "owed_after")):
            put(r, c, tt[key] if vals is not None else None, f_b, right, fmt=MONEY)
        r += 1

    # the box underneath
    r += 1
    sm = s["summary"] or {}
    put(r, 1, "รับ", f_b, center)
    for i, (_, lab) in enumerate(PRODUCTS):
        put(r, 2 + i, lab, f_b, center)
    put(r, 9, "", f_b)
    money = [("รวมขาย", sm.get("amount")), ("วางบิล", sm.get("invoiced")), ("ค้าง (ขายเชื่อ)", sm.get("credit")),
             ("ขายสด", sm.get("cash_sales")), ("จ่ายเก่า", sm.get("old_paid")), ("รวมรับเงิน", sm.get("expected_cash")),
             ("ค้างยกมา", sm.get("owed_before")), ("ค้างยกไป", sm.get("owed_after"))]
    notes = [(f"{n:,} =", (sm.get("notes") or {}).get(str(n))) for n in NOTES] + [("เหรียญ =", sm.get("coins")), ("รวมเงิน", sm.get("counted"))]
    loads = sm.get("loads") or []
    stock = [(str(i + 1), ld) for i, ld in enumerate(loads)] or [("1", {}), ("2", {}), ("3", {})]
    stock += [("รวม", sm.get("loaded") or {}), ("คืน", sm.get("returned") or {}), ("ขายได้", sm.get("sold") or {}),
              ("ขาด(-) / เกิน(+)", sm.get("diff") or {})]
    top = r
    for j, (lab, q) in enumerate(stock, start=1):
        put(top + j, 1, lab, f_n, center)
        for i, k in enumerate(KEYS):
            put(top + j, 2 + i, (q or {}).get(k), f_n, center, fmt=QTY)
    for j, (lab, v) in enumerate(money):
        put(top + j, 10, lab, f_n, left)
        ws.merge_cells(start_row=top + j, start_column=10, end_row=top + j, end_column=11)
        ws.cell(row=top + j, column=11).border = box
        put(top + j, 12, v, f_b, right, fmt=MONEY)
    for j, (lab, v) in enumerate(notes):
        put(top + j, 14, lab, f_n, right)
        put(top + j, 15, v, f_b, right, fmt=MONEY)
    r = top + max(len(stock), len(money), len(notes)) + 2
    km = [("ไมล์ใหม่", sm.get("km_end")), ("ไมล์เก่า", sm.get("km_start")), ("รถวิ่ง (กม.)", sm.get("km"))]
    c = 1
    for lab, v in km:
        put(r, c, lab, f_b, left)
        put(r, c + 1, v, f_n, right, fmt="#,##0")
        c += 3
    put(r, 10, "ตรวจ", f_b, left)
    ws.merge_cells(start_row=r, start_column=11, end_row=r, end_column=12)
    put(r, 11, "", f_n)
    put(r, 13, "ตรวจ", f_b, left)
    ws.merge_cells(start_row=r, start_column=14, end_row=r, end_column=15)
    put(r, 14, "", f_n)
    if sm.get("flags"):
        r += 2
        put(r, 1, "ที่ไม่ตรง: " + " · ".join(sm["flags"]), Font(name=F, size=8), left, False)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
        ws.row_dimensions[r].height = 28

    ws.print_title_rows = "1:3"
    ws.print_area = f"A1:{get_column_letter(ncol)}{r}"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.3
    ws.page_margins.top = ws.page_margins.bottom = 0.4
    ws.oddFooter.center.text = "หน้า &P / &N"
    ws.freeze_panes = "A4"
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
