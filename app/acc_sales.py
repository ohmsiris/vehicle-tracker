"""
บัญชี › รายได้หน้าโรง: the ice program's daily sales per customer (app/sales_report.parse) -> sales_lines rows + the books.

Books: one entry per branch per customer per company month (26th-25th), dated the 25th, party = the customer, so the P&L opens
revenue -> customer -> month. Dr 1200 receivables, Cr 4100 sales.

Ice to the company's other branch is not a sale to a customer: Kaeng Khoi's customers "สระบุรี" and "ทวาย" (Saraburi's ซอง driver
picking up when Saraburi ran short -- Saraburi pays it with the same cheque) and Saraburi's "...แก่งคอย". The making branch books
Dr 1600 / Cr 4150, the receiving branch Dr 5060 / Cr 1600; the cheque that pays it settles 1600 (app/acc_cheques).
Lines worth 0 (tanks lent: ยืมถัง...) are kept in sales_lines only.
"""
from datetime import date

INTERNAL = {"แก่งคอย": ("สระบุรี", "ทวาย"), "สระบุรี": ("แก่งคอย",)}
OTHER = {"สระบุรี": "แก่งคอย", "แก่งคอย": "สระบุรี"}


def company_month(d: str) -> str:
    y, m, dd = (int(x) for x in d.split("-"))
    if dd >= 26:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y}-{m:02d}"


def is_internal(branch: str, customer: str) -> bool:
    c = customer.replace(" ", "")
    if branch == "แก่งคอย":
        return c in INTERNAL["แก่งคอย"] or c.startswith("สระบุรี") and "วารีเทพ" not in c
    return "แก่งคอย" in c


def lines(branch: str, rows: list[dict]) -> list[dict]:
    return [{"branch": branch, "sale_date": r["date"], "month": company_month(r["date"]), "product": r["product"], "customer": r["customer"],
             "qty": r["qty"], "unit": r["unit"], "weight_kg": r["weight"], "price": r["price"], "amount": r["amount"],
             "internal": is_internal(branch, r["customer"]), "source": "program"} for r in rows]


def _line(account, debit, credit, branch):
    return {"account": account, "debit": round(debit, 2), "credit": round(credit, 2), "branch": branch, "vehicle_id": None, "machine_id": None, "memo": None}


def build(branch: str, sales: list[dict], months: tuple[str, str]) -> list[dict]:
    """sales = lines(); only company months first..last (inclusive) are booked."""
    agg: dict = {}
    for s in sales:
        if s["amount"] and months[0] <= s["month"] <= months[1]:
            k = (s["month"], s["customer"], s["internal"])
            agg[k] = round(agg.get(k, 0.0) + s["amount"], 2)
    out = []
    for (month, cust, internal), amt in sorted(agg.items()):
        y, m = (int(x) for x in month.split("-"))
        when = date(y, m, 25).isoformat()
        sid = f"sales:{branch}:{month}:{cust}"
        if internal:
            other = OTHER[branch]
            out.append({"entry_date": when, "branch": branch, "source": "sales_program", "source_id": sid, "party": f"สาขา{other}",
                        "doc_type": "receipt", "doc_no": None, "description": f"ขายน้ำแข็งให้สาขา{other} ({cust}) {m:02d}/{y + 543} · จากโปรแกรมขาย",
                        "lines": [_line("1600", amt, 0, branch), _line("4150", 0, amt, branch)]})
            out.append({"entry_date": when, "branch": other, "source": "sales_program", "source_id": sid + ":other", "party": f"สาขา{branch}",
                        "doc_type": "receipt", "doc_no": None, "description": f"น้ำแข็งจากสาขา{branch} ({cust}) {m:02d}/{y + 543} · จากโปรแกรมขาย",
                        "lines": [_line("5060", amt, 0, other), _line("1600", 0, amt, other)]})
        else:
            out.append({"entry_date": when, "branch": branch, "source": "sales_program", "source_id": sid, "party": cust[:100],
                        "doc_type": "receipt", "doc_no": None, "description": f"ขายน้ำแข็งหน้าโรง {cust} {m:02d}/{y + 543} · จากโปรแกรมขาย",
                        "lines": [_line("1200", amt, 0, branch), _line("4100", 0, amt, branch)]})
    return out
