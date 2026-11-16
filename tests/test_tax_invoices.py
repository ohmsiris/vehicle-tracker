import pytest

from app import tax_invoices as ti, users

CUST = {"P": {"code": "P", "name": "CP ซิลเลอร์", "mode": "kg"}, "07": {"code": "07", "name": "CPF ศูนย์จำหน่าย", "mode": "kg"},
        "02": {"code": "02", "name": "ซันฟู้ด", "mode": "total"}, "18": {"code": "18", "name": "นครหลวงคอนกรีต", "mode": "kg"}}
PRICES = [{"code": "P", "price": 0.45, "from_date": "2025-08-01"}, {"code": "P", "price": 0.51, "from_date": "2026-07-01"},
          {"code": "07", "price": 0.51, "from_date": "2026-07-01"}]


@pytest.mark.parametrize("text, doc_no, kind, code", [
    ("เลขที่   SRI-2569-10001", "SRI-2569-10001", "invoice", "P"),             # five digits after the year: CP ซิลเลอร์
    ("SRI-2569-0710001", "SRI-2569-0710001", "invoice", "07"),
    ("เลขที่   SRI-2569 - 1008009", "SRI-2569-1008009", "invoice", "10"),        # spaces round the dash (typed by hand in the sheet)
    ("เลขที่   SRI-2568-18-06008", "SRI-2568-18-06008", "invoice", "18"),        # the code between dashes
    ("IV-2569-0210", "IV-2569-0210", "invoice", "02"),                            # ซันฟู้ด, one a month
    ("เลขที่   IV-2569-0510001", "IV-2569-0510001", "invoice", "05"),
    ("CN2569/010", "CN2569/010", "credit_note", None),
])
def test_the_number_tells_the_customer(text, doc_no, kind, code):
    p = ti.parse_number(text)
    assert (p["doc_no"], p["kind"], p["code"]) == (doc_no, kind, code)


def test_next_number_keeps_the_width():
    assert ti.next_number("SRI-2569-10009") == "SRI-2569-10010"
    assert ti.next_number("SRI-2569-0710099") == "SRI-2569-0710100"
    assert ti.next_number("CN2569/010") == "CN2569/011"
    assert ti.parse_number("hello") is None


def test_amount_vat_and_total_like_the_sheet():
    assert ti.compute(13800, 0.51) == {"amount": 7038.0, "vat": 492.66, "total": 7530.66}
    assert ti.compute(None, None, 193414.5) == {"amount": 193414.5, "vat": 13539.02, "total": 206953.52}


def test_price_from_its_date():
    assert ti.price_on(PRICES, "P", "2026-06-30") == 0.45
    assert ti.price_on(PRICES, "P", "2026-07-01") == 0.51
    assert ti.price_on(PRICES, "07", "2026-06-01") is None


def test_clean_fills_the_customer_and_price_from_the_number():
    row = ti.clean({"doc_no": "SRI-2569-0711001", "doc_date": "2026-11-03", "kg": "7,800"}, CUST, PRICES)
    assert row["customer_code"] == "07" and row["customer_name"] == "CPF ศูนย์จำหน่าย" and row["month"] == "2026-11"
    assert row["price"] == 0.51 and row["amount"] == 3978.0 and row["vat"] == 278.46 and row["total"] == 4256.46
    sun = ti.clean({"doc_no": "IV-2569-0211", "doc_date": "2026-11-30", "amount": "193414.50", "note": "บด 0.6"}, CUST, PRICES)
    assert sun["kg"] is None and sun["price"] is None and sun["total"] == 206953.52
    cn = ti.clean({"doc_no": "CN2569/011", "doc_date": "2026-11-20", "customer_code": "07", "amount": "826.2"}, CUST, PRICES)
    assert cn["kind"] == "credit_note" and cn["total"] == 884.03


@pytest.mark.parametrize("data, words", [
    ({"doc_no": "ABC", "doc_date": "2026-11-01", "kg": 1}, "เลขที่ไม่ถูกต้อง"),
    ({"doc_no": "SRI-2569-9911001", "doc_date": "2026-11-01", "kg": 1}, "ไม่รู้จักรหัสลูกค้า 99"),
    ({"doc_no": "SRI-2569-10001", "doc_date": "2026-11-01"}, "น้ำหนัก"),
    ({"doc_no": "IV-2569-0211", "doc_date": "2026-11-01"}, "ยอดก่อน VAT"),
    ({"doc_no": "SRI-2568-18-11009", "doc_date": "2026-11-01", "kg": 5}, "ยังไม่มีราคา"),
    ({"doc_no": "SRI-2569-10001", "kg": 5}, "วันที่"),
])
def test_what_is_refused(data, words):
    with pytest.raises(ti.TaxInvoiceError, match=words):
        ti.clean(data, CUST, PRICES)


def test_summary_takes_credit_notes_off_and_finds_doubles_and_gaps():
    rows = [{"kind": "invoice", "doc_no": "SRI-2569-0710001", "customer_code": "07", "customer_name": "x", "kg": 1000, "amount": 510, "vat": 35.7, "total": 545.7},
            {"kind": "invoice", "doc_no": "SRI-2569-0710003", "customer_code": "07", "customer_name": "x", "kg": 1000, "amount": 510, "vat": 35.7, "total": 545.7},
            {"kind": "invoice", "doc_no": "SRI-2569-0710003", "customer_code": "07", "customer_name": "x", "kg": 1000, "amount": 510, "vat": 35.7, "total": 545.7},
            {"kind": "credit_note", "doc_no": "CN2569/010", "customer_code": "07", "customer_name": "x", "kg": 100, "amount": 51, "vat": 3.57, "total": 54.57}]
    s = ti.summarize(rows, CUST)
    c = s["customers"][0]
    assert c["invoices"] == 3 and c["credit_notes"] == 1 and c["kg"] == 2900 and c["total"] == 1582.53
    assert s["duplicates"] == ["SRI-2569-0710003"]
    assert s["gaps"] == [{"series": "SRI-2569-07", "missing": ["SRI-2569-0710002"]}]


def test_the_page_is_a_menu_of_its_own():
    assert users.MENUS["tax_invoices"]["group"] == "บัญชี"
    assert users.allowed(["tax_invoices"], "/api/tax-invoices-setup/prices") and not users.allowed(["payables"], "/api/tax-invoices")
