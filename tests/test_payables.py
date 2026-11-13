from datetime import date

import pytest

from app import payables as pb

TODAY = date(2026, 11, 13)


def _bills():
    t1 = pb.truck_bill({"id": "e1", "event_date": "2026-09-01", "total_cost": 1000, "summary": "เปลี่ยนยาง"}, 0, {"vehicle_number": "16", "plate_number": "1ฒถ 2091", "branch": "สระบุรี"}, "โชคพัฒนา")
    t2 = pb.truck_bill({"id": "e2", "event_date": "2026-10-05", "total_cost": None}, 500.5, None, "โชคพัฒนา")
    p1 = pb.purchase_bill({"id": "p1", "bill_date": "2026-10-20", "bill_no": "IV-1", "total": 2000, "purpose": "stock"}, "วิศวภัณฑ์")
    return [t1, t2, p1]


def test_bills_come_from_both_places_and_skip_what_is_not_owed():
    t1, t2, p1 = _bills()
    assert t1["link"] == "#event=e1" and "#16" in t1["what"] and t1["branch"] == "สระบุรี"
    assert t2["amount"] == 500.5                                                          # no total: the lines add up
    assert p1["bill_no"] == "IV-1" and p1["link"] == "#purchase=p1"
    assert pb.purchase_bill({"id": "p2", "bill_date": "2026-10-20", "total": 900, "purpose": "reference"}, "x") is None   # a price list
    assert pb.truck_bill({"id": "e3", "event_date": "2026-10-01", "total_cost": 0}, 0, None, "x") is None
    assert pb.truck_bill({"id": "e4", "event_date": "2026-10-01", "total_cost": 50}, 0, None, None) is None              # no shop


def test_view_owed_paid_and_shops_paid_on_the_day():
    bills = _bills()
    payments = [{"id": 7, "shop": "โชคพัฒนา", "paid_on": "2026-11-05", "method": "cheque", "amount": 1000, "cheque_no": "123"}]
    links = [{"payment_id": 7, "source": "truck", "bill_id": "e1", "amount": 1000}]
    v = pb.view(bills, payments, links, set(), TODAY)
    chok = next(s for s in v["shops"] if s["shop"] == "โชคพัฒนา")
    assert chok["owed"] == 500.5 and chok["due_count"] == 1 and chok["oldest"] == "2026-10-05"
    assert [b["state"] for b in chok["bills"]] == ["paid", "due"] and chok["payments"][0]["diff"] == 0
    assert v["tiles"]["owed"] == 2500.5 and v["tiles"]["shops"] == 2 and v["tiles"]["paid_month"] == 1000
    assert v["shops"][0]["shop"] == "วิศวภัณฑ์"                                             # the most owed first
    v2 = pb.view(bills, [], [], {"วิศวภัณฑ์"}, TODAY)                                        # paid in cash / by transfer on the day
    assert next(s for s in v2["shops"] if s["shop"] == "วิศวภัณฑ์")["owed"] == 0


def test_a_payment_must_match_its_bills_or_say_why():
    due = {("truck", "e2"): 500.5, ("purchase", "p1"): 2000.0}
    ok = pb.check_payment({"method": "cheque", "amount": "2501", "paid_on": "2026-11-10", "cheque_no": " 0012 ",
                           "bills": [{"source": "truck", "id": "e2"}, {"source": "purchase", "id": "p1"}]}, due)
    assert ok["payment"]["amount"] == 2501 and ok["payment"]["cheque_no"] == "0012" and len(ok["bills"]) == 2      # within ±1
    with pytest.raises(pb.PayablesError, match="ใส่หมายเหตุ"):
        pb.check_payment({"method": "cheque", "amount": 2600, "paid_on": "2026-11-10", "bills": [{"source": "purchase", "id": "p1"}]}, due)
    noted = pb.check_payment({"method": "cheque", "amount": 2600, "paid_on": "2026-11-10", "note": "รวมค่าส่ง",
                              "bills": [{"source": "purchase", "id": "p1"}]}, due)
    assert noted["payment"]["note"] == "รวมค่าส่ง"
    with pytest.raises(pb.PayablesError):                                                   # paid already / another shop's bill
        pb.check_payment({"method": "cheque", "amount": 1, "paid_on": "2026-11-10", "bills": [{"source": "truck", "id": "e1"}]}, due)
    with pytest.raises(pb.PayablesError):
        pb.check_payment({"method": "cheque", "amount": 1, "paid_on": "2026-11-10", "bills": []}, due)
    old = pb.check_payment({"method": "before", "paid_on": "2026-11-13", "bills": [{"source": "truck", "id": "e2"}]}, due)
    assert old["payment"]["amount"] == 500.5                                                # paid before the app: the bills' own total


def test_review_fixes_edited_bill_moved_payment_and_month_tile():
    bills = _bills()
    payments = [{"id": 7, "shop": "ชื่อเก่า", "paid_on": "2026-11-05", "method": "cheque", "amount": 900, "cheque_no": "1"},
                {"id": 8, "shop": "วิศวภัณฑ์", "paid_on": "2026-12-05", "method": "cheque", "amount": 5, "cheque_no": "2"}]   # post-dated
    links = [{"payment_id": 7, "source": "truck", "bill_id": "e1", "amount": 900}]          # e1 is 1,000 now: edited after it was paid
    v = pb.view(bills, payments, links, set(), TODAY)
    chok = next(s for s in v["shops"] if s["shop"] == "โชคพัฒนา")
    assert chok["payments"][0]["id"] == 7                                                    # the payment follows its bill's shop
    assert chok["bills"][0]["changed"] == 100 and chok["flagged"] == 1
    assert not any(s["shop"] == "ชื่อเก่า" for s in v["shops"])
    assert v["tiles"]["paid_month"] == 900                                                  # the December cheque is not this month's
    t = pb.check_payment({"method": "transfer", "amount": 500.5, "paid_on": "2026-11-10", "cheque_no": "9", "book": "HQ",
                          "bills": [{"source": "truck", "id": "e2"}]}, {("truck", "e2"): 500.5})
    assert t["payment"]["cheque_no"] is None and t["payment"]["book"] == "HQ"
