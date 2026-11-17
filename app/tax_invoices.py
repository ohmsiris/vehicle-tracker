"""ใบกำกับภาษี แก่งคอย: every tax invoice and credit note Kaeng Khoi issues, typed by the bookkeeper (owner, 16 Nov 2026) -- the app version
of the Google Sheet "สรุปใบกำกับภาษี แก่งคอย" (one tab per month: number, customer, kg, price per kg, amount, VAT 7%, total).

The customer comes from the invoice number, as the sheet's formula did: SRI-2569-10001 (five digits after the year) is CP ซิลเลอร์;
otherwise the first two digits after the year are the customer's code (SRI-2569-0710001 -> 07 CPF ศูนย์จำหน่าย; IV-2569-0510001 -> 05
ซิลเวอร์ฟาร์ม; SRI-2568-18-06008 -> 18 นครหลวงคอนกรีต; IV-2569-0210 -> 02 ซันฟู้ด). The price per kg is the customer's price on the
invoice's month (tax_invoice_prices, from a date). ซันฟู้ด is billed as one total a month (done on another website): its amount is typed,
not kg x price. A credit note (ใบลดหนี้, CN2569/010) takes money off a customer's month.

Amounts: amount (before VAT) = kg x price, VAT = 7 % of it, total = amount + VAT -- each to the satang. Credit notes are stored as positive
figures and subtracted in the summaries."""
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from app import supabase

BRANCH = "แก่งคอย"
VAT_RATE = 0.07
PRIMARY = "P"              # the code of the invoices with five digits after the year (CP ซิลเลอร์)
MAX_MONEY = 100_000_000


class TaxInvoiceError(Exception):
    pass


# ------------------------------------------------------------------ numbers (pure)
_INV = re.compile(r"(SRI|IV)\s*-\s*(\d{4})\s*-\s*(\d+)(?:\s*-\s*(\d+))?", re.I)
_CN = re.compile(r"CN\s*(\d{4})\s*/\s*(\d+)", re.I)


def parse_number(text: str) -> Optional[dict]:
    """'เลขที่   SRI-2569-0710001' -> {doc_no: 'SRI-2569-0710001', kind: 'invoice', code: '07', series: 'SRI-2569-07', seq: 10001, width: 5}.
    A credit note 'CN2569/010' -> kind 'credit_note' (no code). None when it is neither."""
    t = (text or "").replace("เลขที่", " ").strip()
    m = _CN.search(t)
    if m:
        return {"doc_no": f"CN{m.group(1)}/{m.group(2)}", "kind": "credit_note", "code": None, "series": f"CN{m.group(1)}/",
                "seq": int(m.group(2)), "width": len(m.group(2))}
    m = _INV.search(t)
    if not m:
        return None
    pre, year, a, b = m.group(1).upper(), m.group(2), m.group(3), m.group(4)
    if b is not None:                                  # SRI-2568-18-06008: the code written between dashes
        return {"doc_no": f"{pre}-{year}-{a}-{b}", "kind": "invoice", "code": a[:2], "series": f"{pre}-{year}-{a}-", "seq": int(b), "width": len(b)}
    doc_no = f"{pre}-{year}-{a}"
    if len(a) == 5:
        return {"doc_no": doc_no, "kind": "invoice", "code": PRIMARY, "series": f"{pre}-{year}-", "seq": int(a), "width": 5}
    return {"doc_no": doc_no, "kind": "invoice", "code": a[:2], "series": f"{pre}-{year}-{a[:2]}", "seq": int(a[2:]) if len(a) > 2 else 0,
            "width": max(len(a) - 2, 0)}


def next_number(doc_no: str) -> str:
    """The number after this one, same width: SRI-2569-0710001 -> SRI-2569-0710002, CN2569/010 -> CN2569/011."""
    p = parse_number(doc_no)
    if not p or not p["width"]:
        return ""
    return f"{p['series']}{str(p['seq'] + 1).zfill(p['width'])}"


def money(x) -> float:
    return round(float(x or 0) + 1e-9, 2)


def compute(kg, price, amount=None) -> dict:
    """kg x price (or a typed amount) -> amount, VAT, total, to the satang."""
    if amount is None:
        amount = float(kg or 0) * float(price or 0)
    a = money(amount)
    v = a * VAT_RATE
    # the total as the bill prints it (her Excel adds the unrounded VAT, then shows two decimals): 7,119.60 + 498.372 = 7,617.97
    return {"amount": a, "vat": money(v), "total": money(a + v)}


def price_on(prices: list[dict], code: Optional[str], on: str) -> Optional[float]:
    """The customer's price per kg on a day (ISO): the latest price whose from_date is on or before it."""
    best = None
    for p in prices:
        if p["code"] == code and str(p["from_date"]) <= on and (best is None or str(p["from_date"]) > str(best["from_date"])):
            best = p
    return float(best["price"]) if best else None


def _num(v, label: str, *, allow_none: bool = True) -> Optional[float]:
    if v is None or str(v).strip() == "":
        if allow_none:
            return None
        raise TaxInvoiceError(f"กรุณาใส่{label}")
    try:
        n = float(str(v).replace(",", "").strip())
    except ValueError:
        raise TaxInvoiceError(f"{label}: \"{v}\" ไม่ใช่ตัวเลข")
    if n < 0 or n > MAX_MONEY:
        raise TaxInvoiceError(f"{label}: ตัวเลขไม่ถูกต้อง")
    return n


def clean(data: dict, customers: dict, prices: list[dict]) -> dict:
    """What the page sent -> a row to save. The number decides the kind and (if not chosen) the customer; kg x price unless the customer
    is billed by total (ซันฟู้ด) or the amount was typed (a credit note without kg)."""
    p = parse_number(str(data.get("doc_no") or ""))
    if not p:
        raise TaxInvoiceError("เลขที่ไม่ถูกต้อง — เช่น SRI-2569-10001, IV-2569-0510001 หรือ CN2569/010")
    month = str(data.get("month") or "")
    doc_date = str(data.get("doc_date") or "") or None
    if doc_date:
        try:
            date.fromisoformat(doc_date)
        except ValueError:
            raise TaxInvoiceError("วันที่ไม่ถูกต้อง")
        month = doc_date[:7]
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month):
        raise TaxInvoiceError("กรุณาใส่วันที่ (หรือเดือน) ของใบนี้")
    code = str(data.get("customer_code") or "") or p["code"]
    if code and code not in customers:
        raise TaxInvoiceError(f"ไม่รู้จักรหัสลูกค้า {code} — เพิ่มลูกค้ารหัสนี้ก่อน (หัวข้อ ลูกค้าและราคา)")
    if p["kind"] == "invoice" and not code:
        raise TaxInvoiceError("กรุณาเลือกลูกค้า")
    cust = customers.get(code) or {}
    kg = _num(data.get("kg"), "น้ำหนัก")
    bags = _num(data.get("bags"), "จำนวนก้อน / กระสอบ")
    if p["kind"] == "invoice" and cust.get("entry") == "bags" and cust.get("kg_per_bag") and bags is not None:
        kg = bags * float(cust["kg_per_bag"])                    # ศูนย์จำหน่าย 90 ก้อน x 65 = 5,850 kg; โกลบอล 140 กระสอบ x 25 = 3,500 kg
    price = _num(data.get("price"), "ราคาต่อกิโลกรัม")
    typed = _num(data.get("amount"), "ยอดก่อน VAT")
    by_total = cust.get("mode") == "total"
    if by_total or (p["kind"] == "credit_note" and typed is not None and kg is None):
        if typed is None:
            raise TaxInvoiceError("กรุณาใส่ยอดก่อน VAT")
        sums = compute(None, None, typed)
    else:
        if kg is None:
            raise TaxInvoiceError("กรุณาใส่น้ำหนัก (กก.)")
        if price is None:
            price = price_on(prices, code, doc_date or f"{month}-01")
        if price is None:
            raise TaxInvoiceError(f"ยังไม่มีราคาของ {cust.get('name') or code} — ใส่ราคาต่อกิโลกรัม")
        sums = compute(kg, price)
    return {"branch": BRANCH, "kind": p["kind"], "doc_no": p["doc_no"], "doc_date": doc_date, "month": month, "customer_code": code or None,
            "customer_name": cust.get("name") or str(data.get("customer_name") or "").strip() or None,
            "kg": kg, "price": price if not by_total else None, **sums, "note": (str(data.get("note") or "").strip() or None),
            "item": (item_text(cust, bags if bags is not None else ((kg / float(cust["kg_per_bag"])) if kg and cust.get("kg_per_bag") else None),
                               str(data.get("item") or "").strip()) if p["kind"] == "invoice" else (str(data.get("item") or "").strip() or None)),
            "ticket_no": (str(data.get("ticket_no") or "").strip()[:40] or None)}


def summarize(rows: list[dict], customers: dict) -> dict:
    """A month's figures: tiles, per customer (credit notes taken off), duplicates and gaps in the numbering."""
    by: dict = defaultdict(lambda: {"invoices": 0, "credit_notes": 0, "kg": 0.0, "amount": 0.0, "vat": 0.0, "total": 0.0})
    for r in rows:
        k = r.get("customer_code") or "?"
        s = by[k]
        sign = -1 if r["kind"] == "credit_note" else 1
        s["credit_notes" if sign < 0 else "invoices"] += 1
        s["kg"] += sign * float(r.get("kg") or 0)
        for f in ("amount", "vat", "total"):
            s[f] += sign * float(r.get(f) or 0)
    order = {c: i for i, c in enumerate(customers)}
    per = [{"code": k, "name": (customers.get(k) or {}).get("name") or (next((r["customer_name"] for r in rows if (r.get("customer_code") or "?") == k and r.get("customer_name")), None) or "ไม่ระบุลูกค้า"),
            **{f: (money(v) if isinstance(v, float) else v) for f, v in s.items()}} for k, s in by.items()]
    per.sort(key=lambda x: (order.get(x["code"], 999), x["code"]))
    tot = {f: money(sum(x[f] for x in per)) for f in ("kg", "amount", "vat", "total")}
    tot.update(invoices=sum(x["invoices"] for x in per), credit_notes=sum(x["credit_notes"] for x in per))
    seen, dups = defaultdict(int), []
    series = defaultdict(list)
    for r in rows:
        seen[r["doc_no"]] += 1
        p = parse_number(r["doc_no"])
        if p and p["width"]:
            series[p["series"]].append(p)
    dups = sorted(n for n, c in seen.items() if c > 1)
    gaps = []
    for ser, ps in series.items():
        nums = sorted({p["seq"] for p in ps})
        missing = [n for n in range(nums[0], nums[-1] + 1) if n not in set(nums)] if len(nums) > 1 else []
        if missing and len(missing) <= 30:
            w = ps[0]["width"]
            gaps.append({"series": ser, "missing": [f"{ser}{str(n).zfill(w)}" for n in missing]})
    return {"totals": tot, "customers": per, "duplicates": dups, "gaps": gaps}


# ------------------------------------------------------------------ database
def _customers() -> dict:
    return {c["code"]: c for c in supabase.select_all("tax_invoice_customers", {"select": "*", "order": "sort.asc,code.asc"})}


def _prices() -> list[dict]:
    return supabase.select_all("tax_invoice_prices", {"select": "id,code,price,from_date,note", "order": "from_date.asc,id.asc"})


def _rows(month: str, to: Optional[str] = None) -> list[dict]:
    if to and to != month:
        return supabase.select_all("tax_invoices", {"select": "*", "branch": f"eq.{BRANCH}", "month": f"gte.{month}", "and": f"(month.lte.{to})",
                                                    "order": "month.asc,doc_no.asc,id.asc"})
    return supabase.select_all("tax_invoices", {"select": "*", "branch": f"eq.{BRANCH}", "month": f"eq.{month}", "order": "doc_no.asc,id.asc"})


def months() -> list[str]:
    seen = {r["month"] for r in supabase.select_all("tax_invoices", {"select": "month", "branch": f"eq.{BRANCH}", "order": "month.asc"})}
    return sorted(seen)


def view(month: str, to: Optional[str] = None) -> dict:
    """A month (or months `month`..`to`) of invoices: the rows, the summary by customer and its checks, the customers and their prices."""
    for m in (month, to or month):
        if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", m or ""):
            raise TaxInvoiceError("เดือนไม่ถูกต้อง")
    if to and to < month:
        month, to = to, month
    customers, prices = _customers(), _prices()
    rows = _rows(month, to)
    summary = summarize(rows, customers)
    elsewhere = defaultdict(list)                       # numbers also in a month outside the view
    if not to or to == month:
        mine = {r["doc_no"] for r in rows}
        for r in supabase.select_all("tax_invoices", {"select": "doc_no,month", "branch": f"eq.{BRANCH}", "month": f"neq.{month}", "order": "id.asc"}):
            if r["doc_no"] in mine:
                elsewhere[r["doc_no"]].append(r["month"])
    summary["other_months"] = [{"doc_no": n, "months": sorted(set(ms))} for n, ms in sorted(elsewhere.items())]
    last = max(rows, key=lambda r: r.get("created_at") or "", default=None)
    return {"month": month, "to": to or month, "months": months(), "rows": rows, "summary": summary,
            "customers": list(customers.values()), "prices": prices,
            "templates": {c: {"bill": bool(template(c)), "monthly": bool(template(c, "monthly"))} for c in customers},
            "last": last and {"doc_no": last["doc_no"], "next": next_number(last["doc_no"]), "doc_date": last.get("doc_date")}}


def add(data: dict, who: str) -> dict:
    row = clean(data, _customers(), _prices())
    row["updated_by"] = who
    if not data.get("allow_duplicate") and supabase.select("tax_invoices", {"select": "id", "branch": f"eq.{BRANCH}", "doc_no": f"eq.{row['doc_no']}"}):
        raise TaxInvoiceError(f"เลขที่ {row['doc_no']} มีอยู่แล้ว")
    return supabase.insert("tax_invoices", [row])[0]


def update(row_id: int, data: dict, who: str) -> dict:
    old = supabase.select("tax_invoices", {"select": "*", "id": f"eq.{int(row_id)}"})
    if not old:
        raise TaxInvoiceError("ไม่พบรายการนี้")
    row = clean({**old[0], **data}, _customers(), _prices())
    if row["doc_no"] != old[0]["doc_no"] and supabase.select("tax_invoices", {"select": "id", "branch": f"eq.{BRANCH}", "doc_no": f"eq.{row['doc_no']}"}):
        raise TaxInvoiceError(f"เลขที่ {row['doc_no']} มีอยู่แล้ว")
    row.update(updated_by=who, updated_at=datetime.now(timezone.utc).isoformat())
    return supabase.update("tax_invoices", {"id": f"eq.{int(row_id)}"}, row)[0]


def delete(row_id: int) -> None:
    supabase.delete("tax_invoices", {"id": f"eq.{int(row_id)}"})


def save_customer(data: dict) -> dict:
    code = str(data.get("code") or "").strip().upper()
    if not re.fullmatch(r"P|\d{2}", code):
        raise TaxInvoiceError("รหัสลูกค้า = เลข 2 หลักหลังปีในเลขที่ใบกำกับ (เช่น 07)")
    name = str(data.get("name") or "").strip()
    if not name:
        raise TaxInvoiceError("กรุณาใส่ชื่อลูกค้า")
    mode = "total" if data.get("mode") == "total" else "kg"
    row = {"code": code, "name": name, "mode": mode, "unit": str(data.get("unit") or "กก.").strip()[:20] or "กก.",
           "sort": int(data.get("sort") or 50), "active": data.get("active") is not False}
    return supabase.insert("tax_invoice_customers", [row], on_conflict="code")[0]


def add_price(data: dict) -> dict:
    code = str(data.get("code") or "")
    if code not in _customers():
        raise TaxInvoiceError("ไม่รู้จักลูกค้านี้")
    price = _num(data.get("price"), "ราคา", allow_none=False)
    try:
        frm = date.fromisoformat(str(data.get("from_date") or "")).isoformat()
    except ValueError:
        raise TaxInvoiceError("กรุณาใส่วันที่เริ่มใช้ราคา")
    return supabase.insert("tax_invoice_prices", [{"code": code, "price": price, "from_date": frm, "note": str(data.get("note") or "").strip() or None}])[0]


def delete_price(price_id: int) -> None:
    supabase.delete("tax_invoice_prices", {"id": f"eq.{int(price_id)}"})


def book() -> dict:
    """{calendar month: {"cp" | "cpf_other": amount before VAT}} -- what acc_cp_invoice.read_book gives from the sheet, from the app."""
    from app.acc_cp_invoice import invoice_channel
    out: dict = defaultdict(lambda: defaultdict(float))
    for r in supabase.select_all("tax_invoices", {"select": "month,kind,customer_name,amount", "branch": f"eq.{BRANCH}", "order": "id.asc"}):
        name = r.get("customer_name") or ""
        ch = invoice_channel(("ใบลดหนี้" + name) if r["kind"] == "credit_note" else name)
        if ch:
            out[r["month"]][ch] += (-1 if r["kind"] == "credit_note" else 1) * float(r["amount"] or 0)
    return {m: dict(v) for m, v in out.items()}


# ------------------------------------------------------------------ the next number, and the Excel bills (16 Nov 2026)
def format_number(fmt: str, day: date, seq: int) -> str:
    """'SRI-{be}-{mm}{seq:03d}' on 16/11/2026, 153 -> 'SRI-2569-11153'."""
    return fmt.format(be=day.year + 543, mm=f"{day.month:02d}", seq=seq)


def next_for(cust: dict, day: date, used: list[str]) -> Optional[str]:
    """The number the next bill of this customer on this day gets: one after the highest of this month's (or, for customers whose count
    runs on through the year, of all of its numbers in that series), skipping none. `used` = this customer's numbers so far."""
    fmt = cust.get("number_format")
    if not fmt:
        return None
    if "{seq" not in fmt:
        return format_number(fmt, day, 0)
    head = fmt.split("{seq")[0]
    width = int(re.search(r"\{seq:0(\d)d\}", fmt).group(1)) if re.search(r"\{seq:0(\d)d\}", fmt) else 3
    if cust.get("seq_monthly"):
        prefix = format_number(head, day, 0)                       # this month's series: SRI-2569-11
        seqs = [int(n[len(prefix):]) for n in used if n.startswith(prefix) and n[len(prefix):].isdigit() and len(n) - len(prefix) == width]
    else:
        prefix = format_number(head.split("{mm}")[0], day, 0)      # the year's series, whatever the month: SRI-2569-06
        seqs = [int(n[-width:]) for n in used if n.startswith(prefix) and n[-width:].isdigit()]
    return format_number(fmt, day, (max(seqs) if seqs else 0) + 1)


def next_number_for(code: str, day_text: str) -> dict:
    customers = _customers()
    cust = customers.get(code)
    if not cust:
        raise TaxInvoiceError("ไม่รู้จักลูกค้านี้")
    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        raise TaxInvoiceError("วันที่ไม่ถูกต้อง")
    used = [r["doc_no"] for r in supabase.select_all("tax_invoices", {"select": "doc_no", "branch": f"eq.{BRANCH}", "customer_code": f"eq.{code}",
                                                                     "kind": "eq.invoice", "order": "id.asc"})]
    return {"code": code, "doc_no": next_for(cust, day, used)}


TEMPLATES = __import__("pathlib").Path(__file__).resolve().parent / "templates"
TH_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
TH_SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def template(code: str, kind: str = "bill"):
    """Her own Excel for this customer: bill_<code>.xlsx (one bill) or monthly_<code>.xlsx (the month's ใบวางบิล); None when there is none
    (ซันฟู้ด -- done by the owner's father -- and the occasional customers are typed only)."""
    f = TEMPLATES / f"{'bill' if kind == 'bill' else 'monthly'}_{code}.xlsx"
    return f if f.exists() else None


def item_text(cust: dict, bags=None, chosen: str = "") -> Optional[str]:
    """What goes in รายการ: the chosen item (CP: 200 / 100 ก้อน), or the customer's wording with the number of bags filled in."""
    if chosen:
        return chosen
    fmt = cust.get("item_format")
    if fmt:
        n = "" if bags is None else (int(float(bags)) if float(bags) == int(float(bags)) else bags)
        return fmt.replace("{bags}", str(n))
    items = cust.get("items") or []
    return items[0] if items else None


def sheet_title(doc_no: str) -> str:
    """The tab name the bookkeeper used: SRI-2569-11152 -> CPF01-11152-2026, SRI-2569-0711022 -> CPF0711022-2026."""
    m = re.fullmatch(r"SRI-(\d{4})-(\d+)", doc_no)
    if m:
        tail, year = m.group(2), int(m.group(1)) - 543
        return f"CPF01-{tail}-{year}" if len(tail) == 5 else f"CPF{tail}-{year}"
    return re.sub(r"[\\/*?:\[\]]", "-", doc_no)[:31]


def _workbook(path):
    import openpyxl
    wb = openpyxl.load_workbook(path)
    for n in list(wb.defined_names):
        if n.startswith("_xlfn"):
            del wb.defined_names[n]
    return wb, wb.worksheets[0]


_REF = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")


def _evaluate(ws) -> None:
    """Her sums and VAT written as numbers (16 Nov 2026): a downloaded file opens in Excel's Protected View, which does not calculate --
    the amounts were blank. The formulas are the simple ones of her sheets (=C10*E10, =SUM(I5:I31), =I32*7%); anything else is left."""
    from openpyxl.utils import column_index_from_string
    cache, busy = {}, set()

    def val(coord):
        if coord in cache:
            return cache[coord]
        if coord in busy:
            raise ValueError("loop")
        v = ws[coord].value
        if isinstance(v, str) and v.startswith("="):
            busy.add(coord)
            v = calc(v[1:])
            busy.discard(coord)
        cache[coord] = v
        return v

    def num(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    def calc(expr):
        def rng(m):
            c0, r0, c1, r1 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
            total = 0.0
            for ci in range(column_index_from_string(c0), column_index_from_string(c1) + 1):
                for r in range(r0, r1 + 1):
                    total += num(val(f"{ws.cell(r, ci).column_letter}{r}"))
            return repr(total)
        e = re.sub(r"SUM\(\$?([A-Z]{1,3})\$?(\d+):\$?([A-Z]{1,3})\$?(\d+)\)", rng, expr.upper())
        e = _REF.sub(lambda m: repr(num(val(f"{m.group(1)}{m.group(2)}"))), e).replace("%", "/100")
        if not re.fullmatch(r"[0-9eE.+\-*/() ]+", e):
            raise ValueError("not a sum")
        return eval(e, {"__builtins__": {}}, {})          # digits and + - * / ( ) only (checked just above)

    from decimal import ROUND_HALF_UP, Decimal
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                try:
                    v = val(cell.coordinate)
                except Exception:                            # noqa: BLE001 -- not one of her simple sums: keep the formula
                    continue
                # to the satang, as it prints (83.47499999 -> 83.48): the stored figure, the printed one and the words agree
                cell.value = float(Decimal(repr(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if isinstance(v, float) else v


def _excel_safe(data: bytes) -> bytes:
    """Excel (not LibreOffice) calls a file damaged, "repairs" it and drops the column widths, when a cell format points at a style that
    is not there -- her bills, converted from .xls, point at style 15 of 6. Point those at the plain style."""
    import io
    import zipfile
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in src.infolist():
            body = src.read(item.filename)
            if item.filename == "xl/styles.xml":
                xml = body.decode("utf-8")
                parts = {}
                for tag in ("fonts", "fills", "borders", "cellStyleXfs"):
                    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", xml, re.S)
                    child = {"fonts": "font", "fills": "fill", "borders": "border", "cellStyleXfs": "xf"}[tag]
                    parts[tag] = len(re.findall(rf"<{child}\b", m.group(1))) if m else 0
                m = re.search(r"(<cellXfs\b[^>]*>)(.*?)(</cellXfs>)", xml, re.S)
                if m:
                    def fix(x):
                        tag = x.group(0)
                        for attr, limit in (("xfId", parts["cellStyleXfs"]), ("fontId", parts["fonts"]), ("fillId", parts["fills"]), ("borderId", parts["borders"])):
                            tag = re.sub(rf'{attr}="(\d+)"', lambda a: f'{attr}="{a.group(1) if int(a.group(1)) < max(limit, 1) else 0}"', tag)
                        return tag
                    xml = xml[:m.start(2)] + re.sub(r"<xf\b[^>]*>", fix, m.group(2)) + xml[m.end(2):]
                body = xml.encode("utf-8")
            z.writestr(item, body)
    return out.getvalue()


def _save(wb) -> bytes:
    import io
    for ws in wb.worksheets:
        _evaluate(ws)
    for v in wb.views:                                         # her workbook had many tabs: "first tab shown" = 1 is past the only one,
        v.firstSheet = 0                                       # which Excel (not LibreOffice) treats as damage
        v.activeTab = 0
    wb.calculation.fullCalcOnLoad = True
    buf = io.BytesIO()
    wb.save(buf)
    return _excel_safe(buf.getvalue())


def _month_end(month: str) -> date:
    import datetime as _dt
    y, m = int(month[:4]), int(month[5:7])
    return date(y + (m == 12), m % 12 + 1, 1) - _dt.timedelta(days=1)


BILL_LAST_COL, BILL_ROWS = "F", 18          # the bill box: A1:F18 (ผู้รับเงิน/ผู้ขาย is row 18)
# The EPSON LQ-310's 9 x 5.5" paper is the driver's paper no. 167 -- what her own bill files carry and what Excel saved when the owner
# picked 9x5.5 (17 Nov 2026). The templates came through LibreOffice as A4 (9); with 167 the file opens on 9 x 5.5 for that printer.
BILL_PAPER = 167


def bills_workbook(rows: list[dict], path) -> bytes:
    """The bills as her Excel file, all on ONE tab (owner, 16 Nov 2026: one print prints them all): her own bill (same page set-up, fonts,
    columns, margins, print area width and centring -- so it lands on the 9 x 5.5" paper exactly where it does now) repeated down the
    sheet, one bill per printed page (a page break after each). Only the number, the date, the item, the kg and the price change -- and
    the amount in words (BAHTTEXT's text)."""
    from copy import copy
    from openpyxl.formula.translate import Translator
    from openpyxl.utils import column_index_from_string
    from openpyxl.worksheet.pagebreak import Break
    from app.bahttext import bahttext
    wb, ws = _workbook(path)
    # The bill is A1:F18. Her print area (A1:M20) is wider and taller than the 9 x 5.5" page: Excel printed each bill plus a blank page
    # (rows 19-20 below it) and a blank page for G-M beside it -- 13 bills gave 52 pages (owner, 17 Nov 2026). So the file prints A-F,
    # 18 rows a bill, a page break after each: one bill = one page.
    last_col, block = BILL_LAST_COL, BILL_ROWS
    ncol = column_index_from_string(last_col)
    snap = {(r, c): (ws.cell(r, c).value, copy(ws.cell(r, c).font), copy(ws.cell(r, c).border), copy(ws.cell(r, c).fill),
                     ws.cell(r, c).number_format, copy(ws.cell(r, c).alignment), copy(ws.cell(r, c).protection))
            for r in range(1, block + 1) for c in range(1, ncol + 1)}
    heights = {r: ws.row_dimensions[r].height for r in range(1, block + 1)}
    merges = [(mg.min_row, mg.min_col, mg.max_row, mg.max_col) for mg in ws.merged_cells.ranges if mg.max_row <= block]
    bills = sorted(rows, key=lambda x: x["doc_no"])
    for k, r in enumerate(bills):
        off = k * block
        if k:                                                    # her bill again, under the last one
            for (rr, c), (v, font, border, fill, fmt, align, prot) in snap.items():
                cell = ws.cell(rr + off, c)
                if isinstance(v, str) and v.startswith("="):
                    v = Translator(v, origin=f"{cell.column_letter}{rr}").translate_formula(f"{cell.column_letter}{rr + off}")
                cell.value = v
                cell.font, cell.border, cell.fill, cell.number_format, cell.alignment, cell.protection = font, border, fill, fmt, align, prot
            for rr, h in heights.items():
                ws.row_dimensions[rr + off].height = h
            for r0, c0, r1, c1 in merges:
                ws.merge_cells(start_row=r0 + off, start_column=c0, end_row=r1 + off, end_column=c1)
            ws.row_breaks.append(Break(id=off))                 # each bill starts a new page
        d = date.fromisoformat(r["doc_date"])
        ws[f"E{6 + off}"] = f"เลขที่   {r['doc_no']}"
        ws[f"E{7 + off}"] = f"วันที่  {d.day:02d}/{d.month:02d}/{d.year + 543}"
        ws[f"B{10 + off}"] = r.get("item") or snap[(10, 2)][0]
        ws[f"C{10 + off}"] = float(r["kg"])
        ws[f"E{10 + off}"] = float(r["price"])
        ws[f"A{15 + off}"] = f"({bahttext(r['total'])})"
        for extra in (11, 12):                                  # her two spare item lines: =C11*E11 of nothing -- left empty, not a 0
            if not ws[f"C{extra + off}"].value:
                ws[f"F{extra + off}"] = None
    first, last = bills[0]["doc_no"], bills[-1]["doc_no"]
    ws.title = sheet_title(first) if len(bills) == 1 else (sheet_title(first) + "-" + last[-3:])[:31]
    ws.print_area = f"A1:{last_col}{block * len(bills)}"
    ws.page_setup.paperSize = BILL_PAPER
    return _save(wb)


def bills_file(ids: list[int]) -> tuple[bytes, str]:
    if not ids:
        raise TaxInvoiceError("ยังไม่ได้เลือกบิล")
    rows = []
    for i in range(0, len(ids), 100):
        rows += supabase.select_all("tax_invoices", {"select": "*", "id": f"in.({','.join(str(int(x)) for x in ids[i:i + 100])})", "order": "id.asc"})
    codes = {r.get("customer_code") for r in rows}
    if len(codes) != 1:
        raise TaxInvoiceError("ทำไฟล์บิลได้ทีละลูกค้า")
    code = codes.pop()
    path = template(code)
    if not path:
        raise TaxInvoiceError("ลูกค้านี้ไม่มีแบบฟอร์มบิลในโปรแกรม")
    bad = [r["doc_no"] for r in rows if r["kind"] != "invoice" or not r.get("doc_date") or r.get("kg") is None or r.get("price") is None]
    if bad:
        raise TaxInvoiceError(f"ทำไฟล์บิลได้เฉพาะใบกำกับที่มีวันที่ น้ำหนัก และราคา — {', '.join(bad[:5])}")
    if len(rows) > 400:
        raise TaxInvoiceError("เลือกได้ครั้งละไม่เกิน 400 บิล")
    name = (_customers().get(code) or {}).get("name") or code
    days = sorted({r["doc_date"] for r in rows})
    return bills_workbook(rows, path), f"บิล {name} {days[0]}" + (f" ถึง {days[-1]}" if len(days) > 1 else "") + ".xlsx"


# ------------------------------------------------------------------ the monthly customers: deliveries, and the month's ใบวางบิล
def clean_delivery(data: dict, cust: dict, prices: list[dict]) -> dict:
    try:
        day = date.fromisoformat(str(data.get("day") or ""))
    except ValueError:
        raise TaxInvoiceError("วันที่ไม่ถูกต้อง")
    bags = _num(data.get("bags"), "จำนวนกระสอบ")
    kg = _num(data.get("kg"), "น้ำหนัก")
    if cust.get("entry") == "bags" and cust.get("kg_per_bag"):
        if bags is None:
            raise TaxInvoiceError("กรุณาใส่จำนวนกระสอบ")
        kg = bags * float(cust["kg_per_bag"])
    if not kg:
        raise TaxInvoiceError("กรุณาใส่น้ำหนัก (กก.)")
    price = _num(data.get("price"), "ราคา") or price_on(prices, cust["code"], day.isoformat())
    if price is None:
        raise TaxInvoiceError(f"ยังไม่มีราคาของ {cust['name']}")
    return {"customer_code": cust["code"], "day": day.isoformat(), "note_no": (str(data.get("note_no") or "").strip()[:40] or None),
            "item": item_text(cust, bags, str(data.get("item") or "").strip()), "bags": bags, "kg": kg, "price": price,
            "amount": money(kg * price)}


def next_note(notes: list) -> str:
    """The delivery note after the last one: 4733/236628 -> 4733/236629."""
    for n in reversed(notes):
        m = re.fullmatch(r"(.*?)(\d+)", n or "")
        if m:
            return f"{m.group(1)}{str(int(m.group(2)) + 1).zfill(len(m.group(2)))}"
    return ""


def deliveries(code: str, month: str) -> dict:
    """A monthly customer's deliveries not yet on a ใบวางบิล (any month) and those on this month's, with the next delivery-note number."""
    cust = _customers().get(code)
    if not cust or cust.get("billing") != "monthly":
        raise TaxInvoiceError("ลูกค้านี้ไม่ได้วางบิลรายเดือน")
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month or ""):
        raise TaxInvoiceError("เดือนไม่ถูกต้อง")
    rows = supabase.select_all("tax_invoice_deliveries", {"select": "*", "customer_code": f"eq.{code}", "order": "day.asc,id.asc"})
    bill = supabase.select("tax_invoices", {"select": "id,doc_no,doc_date,kg,amount,vat,total", "customer_code": f"eq.{code}", "month": f"eq.{month}",
                                            "kind": "eq.invoice", "order": "id.asc"})
    bill_ids = {b["id"] for b in bill}
    return {"code": code, "month": month, "open": [r for r in rows if not r.get("invoice_id")],
            "billed": [r for r in rows if r.get("invoice_id") in bill_ids], "bill": bill[0] if bill else None,
            "next_note": next_note([r.get("note_no") for r in rows])}


def add_delivery(data: dict, who: str) -> dict:
    cust = _customers().get(str(data.get("customer_code") or ""))
    if not cust or cust.get("billing") != "monthly":
        raise TaxInvoiceError("ลูกค้านี้ไม่ได้วางบิลรายเดือน")
    row = clean_delivery(data, cust, _prices())
    row["updated_by"] = who
    return supabase.insert("tax_invoice_deliveries", [row])[0]


def update_delivery(row_id: int, data: dict, who: str) -> dict:
    old = supabase.select("tax_invoice_deliveries", {"select": "*", "id": f"eq.{int(row_id)}"})
    if not old:
        raise TaxInvoiceError("ไม่พบรายการนี้")
    if old[0].get("invoice_id"):
        raise TaxInvoiceError("รายการนี้อยู่ในใบวางบิลแล้ว — ยกเลิกใบวางบิลก่อนแก้")
    cust = _customers()[old[0]["customer_code"]]
    row = clean_delivery({**old[0], **data}, cust, _prices())
    row["updated_by"] = who
    return supabase.update("tax_invoice_deliveries", {"id": f"eq.{int(row_id)}"}, row)[0]


def delete_delivery(row_id: int) -> None:
    old = supabase.select("tax_invoice_deliveries", {"select": "invoice_id", "id": f"eq.{int(row_id)}"})
    if old and old[0].get("invoice_id"):
        raise TaxInvoiceError("รายการนี้อยู่ในใบวางบิลแล้ว — ยกเลิกใบวางบิลก่อนลบ")
    supabase.delete("tax_invoice_deliveries", {"id": f"eq.{int(row_id)}"})


def make_monthly(code: str, month: str, delivery_ids: list, who: str) -> dict:
    """The month's ใบวางบิล: one tax invoice (the next number) for these deliveries, which are then on it."""
    cust = _customers().get(code)
    if not cust or cust.get("billing") != "monthly":
        raise TaxInvoiceError("ลูกค้านี้ไม่ได้วางบิลรายเดือน")
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month or ""):
        raise TaxInvoiceError("เดือนไม่ถูกต้อง")
    if supabase.select("tax_invoices", {"select": "id", "customer_code": f"eq.{code}", "month": f"eq.{month}", "kind": "eq.invoice"}):
        raise TaxInvoiceError("เดือนนี้มีใบวางบิลแล้ว")
    ids = [int(x) for x in delivery_ids or []]
    if not ids:
        raise TaxInvoiceError("ยังไม่ได้เลือกรายการส่งของ")
    rows = supabase.select_all("tax_invoice_deliveries", {"select": "*", "id": f"in.({','.join(map(str, ids))})", "order": "day.asc,id.asc"})
    if len(rows) != len(set(ids)) or any(r["customer_code"] != code or r.get("invoice_id") for r in rows):
        raise TaxInvoiceError("มีรายการที่อยู่ในใบวางบิลอื่นแล้ว หรือไม่ใช่ของลูกค้านี้ — โหลดหน้าใหม่แล้วลองอีกครั้ง")
    last = _month_end(month)
    used = [r["doc_no"] for r in supabase.select_all("tax_invoices", {"select": "doc_no", "customer_code": f"eq.{code}", "kind": "eq.invoice", "order": "id.asc"})]
    doc_no = next_for(cust, last, used)
    sums = compute(None, None, sum(float(r["amount"]) for r in rows))
    prices = {float(r["price"]) for r in rows}
    inv = supabase.insert("tax_invoices", [{"branch": BRANCH, "kind": "invoice", "doc_no": doc_no, "doc_date": last.isoformat(), "month": month,
                                             "customer_code": code, "customer_name": cust["name"], "kg": sum(float(r["kg"]) for r in rows),
                                             "price": prices.pop() if len(prices) == 1 else None, **sums, "item": cust.get("item_format"),
                                             "note": f"ใบวางบิล {len(rows)} รายการส่งของ", "source": "app", "updated_by": who}])[0]
    supabase.update("tax_invoice_deliveries", {"id": f"in.({','.join(map(str, ids))})"}, {"invoice_id": inv["id"]})
    return inv


def undo_monthly(invoice_id: int) -> None:
    """Take a ใบวางบิล back (its deliveries become open again) -- to fix a delivery and make it again."""
    supabase.update("tax_invoice_deliveries", {"invoice_id": f"eq.{int(invoice_id)}"}, {"invoice_id": None})
    supabase.delete("tax_invoices", {"id": f"eq.{int(invoice_id)}"})


def shift_formula(f, data_end_old: int, data_end_new: int, block_from: int, delta: int):
    """A totals-row formula of her sheet, moved: the data range ends on the new last row; a cell of the totals block moves with it.
    '=SUM(E5:E31)' -> '=SUM(E5:E20)'; '=J32*7%' (J32 in the block) -> '=J21*7%'."""
    if not isinstance(f, str) or not f.startswith("="):
        return f
    f = re.sub(rf"(:\$?[A-Z]+\$?){data_end_old}(?!\d)", lambda m: f"{m.group(1)}{data_end_new}", f)
    return re.sub(r"(?<![A-Z$])(\$?[A-Z]{1,2}\$?)(\d+)(?!\d)",
                  lambda m: f"{m.group(1)}{int(m.group(2)) + delta}" if int(m.group(2)) >= block_from else m.group(0), f)


def monthly_workbook(inv: dict, rows: list[dict], path) -> bytes:
    """The month's ใบวางบิล in her own sheet: the heading and number, then one row per delivery (her row 5's look and formulas), then her
    totals / VAT / net rows and the signatures, moved to sit right under the last delivery. Dates are written as she types them (year 25xx)."""
    import datetime as _dt
    from copy import copy
    from openpyxl.formula.translate import Translator
    wb, ws = _workbook(path)
    first = 5
    end_old = max(r for r in range(first, ws.max_row + 1) if hasattr(ws.cell(r, 2).value, "year"))
    max_col = max(ws.max_column, 10)
    style = lambda c: (copy(c.font), copy(c.border), copy(c.fill), c.number_format, copy(c.alignment))  # noqa: E731
    proto = [(ws.cell(first, c).value, style(ws.cell(first, c))) for c in range(1, max_col + 1)]
    proto_last = [style(ws.cell(end_old, c)) for c in range(1, max_col + 1)]       # her last row closes the table (its bottom line)
    proto_h = ws.row_dimensions[first].height
    tail_from, tail_to = end_old + 1, ws.max_row
    tail = {(r - tail_from, c): (ws.cell(r, c).value, style(ws.cell(r, c))) for r in range(tail_from, tail_to + 1) for c in range(1, max_col + 1)}
    tail_h = {r - tail_from: ws.row_dimensions[r].height for r in range(tail_from, tail_to + 1)}
    tail_merges = [(m.min_row, m.min_col, m.max_row, m.max_col) for m in list(ws.merged_cells.ranges) if m.min_row >= tail_from]
    for m in list(ws.merged_cells.ranges):
        if m.min_row >= first:
            ws.unmerge_cells(str(m))
    for r in range(first, tail_to + 1):
        for c in range(1, max_col + 1):
            ws.cell(r, c).value = None
    end_new = first + len(rows) - 1
    delta = end_new - end_old

    def put(cell, st):
        cell.font, cell.border, cell.fill, cell.number_format, cell.alignment = st
    for i, d in enumerate(rows):
        r = first + i
        day = date.fromisoformat(d["day"])
        vals = {1: i + 1, 2: _dt.datetime(day.year + 543, day.month, day.day), 3: d.get("note_no"), 4: d.get("item"),
                5: float(d["bags"]) if d.get("bags") is not None else None, 6: float(d["kg"]), 8: float(d["price"])}
        for c in range(1, max_col + 1):
            v0, st = proto[c - 1]
            cell = ws.cell(r, c)
            put(cell, proto_last[c - 1] if i == len(rows) - 1 else st)
            col = cell.column_letter
            if isinstance(v0, str) and v0.startswith("="):             # her formulas: kg = bags x 20, amount = kg x price
                cell.value = Translator(v0, origin=f"{col}{first}").translate_formula(f"{col}{r}")
            elif c in vals:
                cell.value = vals[c]
            else:
                cell.value = v0 if c == 7 else None                     # the unit (Kg)
        ws.row_dimensions[r].height = proto_h
    for (dr, c), (v, st) in tail.items():
        cell = ws.cell(end_new + 1 + dr, c)
        cell.value = shift_formula(v, end_old, end_new, tail_from, delta)
        put(cell, st)
    for dr, h in tail_h.items():
        ws.row_dimensions[end_new + 1 + dr].height = h
    for r0, c0, r1, c1 in tail_merges:
        ws.merge_cells(start_row=r0 + delta, start_column=c0, end_row=r1 + delta, end_column=c1)
    for r in range(end_new + 1 + (tail_to - tail_from) + 1, ws.max_row + 1):
        for c in range(1, max_col + 1):
            ws.cell(r, c).value = None
    last = _month_end(inv["month"])
    y, mo = last.year, last.month
    ws["B2"] = f"ใบวางบิล การจัดส่ง-น้ำแข็ง  วันที่ 1-{last.day} {TH_MONTHS[mo - 1]}  {y + 543}"
    if ws["H2"].value:
        ws["H2"] = f"เลขที่ใบวางบิล : {inv['doc_no']}"
    area = (ws.print_area or "").split("!")[-1].replace("$", "")
    m = re.fullmatch(r"([A-Z]+)1:([A-Z]+)(\d+)", area)
    ws.title = f"1-{last.day} {TH_SHORT[mo - 1]}{y + 543}"
    ws.print_area = f"A1:{m.group(2)}{int(m.group(3)) + delta}" if m else None
    return _save(wb)


def monthly_file(invoice_id: int) -> tuple[bytes, str]:
    inv = supabase.select("tax_invoices", {"select": "*", "id": f"eq.{int(invoice_id)}"})
    if not inv:
        raise TaxInvoiceError("ไม่พบใบวางบิล")
    inv = inv[0]
    path = template(inv["customer_code"], "monthly")
    if not path:
        raise TaxInvoiceError("ลูกค้านี้ไม่มีแบบฟอร์มใบวางบิลในโปรแกรม")
    rows = supabase.select_all("tax_invoice_deliveries", {"select": "*", "invoice_id": f"eq.{inv['id']}", "order": "day.asc,id.asc"})
    if not rows:
        raise TaxInvoiceError("ใบวางบิลนี้ไม่มีรายการส่งของ")
    return monthly_workbook(inv, rows, path), f"ใบวางบิล {inv.get('customer_name') or ''} {inv['month']}.xlsx"


def set_printed(ids: list, printed: bool) -> dict:
    """Tick (or untick) "พิมพ์แล้ว" on these bills."""
    from datetime import datetime as _dt, timezone as _tz
    ids = [int(x) for x in ids or []]
    if not ids:
        raise TaxInvoiceError("ยังไม่ได้เลือกบิล")
    when = _dt.now(_tz.utc).isoformat() if printed else None
    for i in range(0, len(ids), 200):
        supabase.update("tax_invoices", {"id": f"in.({','.join(map(str, ids[i:i + 200]))})"}, {"printed_at": when})
    return {"ids": ids, "printed_at": when}
