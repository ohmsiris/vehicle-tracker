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
    v = money(a * VAT_RATE)
    return {"amount": a, "vat": v, "total": money(a + v)}


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
            "kg": kg, "price": price if not by_total else None, **sums, "note": (str(data.get("note") or "").strip() or None)}


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


def _rows(month: str) -> list[dict]:
    return supabase.select_all("tax_invoices", {"select": "*", "branch": f"eq.{BRANCH}", "month": f"eq.{month}", "order": "doc_no.asc,id.asc"})


def months() -> list[str]:
    seen = {r["month"] for r in supabase.select_all("tax_invoices", {"select": "month", "branch": f"eq.{BRANCH}", "order": "month.asc"})}
    return sorted(seen)


def view(month: str) -> dict:
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month or ""):
        raise TaxInvoiceError("เดือนไม่ถูกต้อง")
    customers, prices = _customers(), _prices()
    rows = _rows(month)
    mine = {r["doc_no"] for r in rows}
    elsewhere = defaultdict(list)                       # this month's numbers that are also in another month
    for r in supabase.select_all("tax_invoices", {"select": "doc_no,month", "branch": f"eq.{BRANCH}", "month": f"neq.{month}", "order": "id.asc"}):
        if r["doc_no"] in mine:
            elsewhere[r["doc_no"]].append(r["month"])
    summary = summarize(rows, customers)
    summary["other_months"] = [{"doc_no": n, "months": sorted(set(ms))} for n, ms in sorted(elsewhere.items())]
    last = max(rows, key=lambda r: r.get("created_at") or "", default=None)
    return {"month": month, "months": months(), "rows": rows, "summary": summary,
            "customers": list(customers.values()), "prices": prices,
            "prices_now": {c: price_on(prices, c, f"{month}-28") for c in customers},
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
