from app import acc_sales as s


def _r(d, cust, amt):
    return {"date": d, "product": "ซอง", "customer": cust, "qty": 1, "unit": "ซอง", "weight": 0, "price": amt, "amount": amt}


def test_company_month_and_internal():
    assert s.company_month("2026-01-26") == "2026-02" and s.company_month("2026-12-26") == "2027-01" and s.company_month("2026-02-25") == "2026-02"
    assert s.is_internal("แก่งคอย", "สระบุรี") and s.is_internal("แก่งคอย", "ทวาย") and not s.is_internal("แก่งคอย", "CPF ซิลเลอร์")
    assert s.is_internal("สระบุรี", "สระบุรีวารีเทพ(แก่งคอย)") and not s.is_internal("สระบุรี", "ร้านเปรม")


def test_build_customer_month_and_between_branches():
    rows = s.lines("แก่งคอย", [_r("2026-01-02", "CPF ซิลเลอร์", 100.0), _r("2026-01-20", "CPF ซิลเลอร์", 50.0), _r("2026-01-27", "CPF ซิลเลอร์", 7.0),
                                _r("2026-01-05", "ทวาย", 30.0), {**_r("2026-01-05", "ร้านเฒ่า", 0.0), "product": "ยืมถัง200ลิตร"}])
    es = s.build("แก่งคอย", rows, ("2026-01", "2026-01"))
    by = {e["source_id"]: [(ln["account"], ln["debit"], ln["credit"], ln["branch"]) for ln in e["lines"]] for e in es}
    assert by["sales:แก่งคอย:2026-01:CPF ซิลเลอร์"] == [("1200", 150.0, 0, "แก่งคอย"), ("4100", 0, 150.0, "แก่งคอย")]
    assert by["sales:แก่งคอย:2026-01:ทวาย"] == [("1600", 30.0, 0, "แก่งคอย"), ("4150", 0, 30.0, "แก่งคอย")]
    assert by["sales:แก่งคอย:2026-01:ทวาย:other"] == [("5060", 30.0, 0, "สระบุรี"), ("1600", 0, 30.0, "สระบุรี")]
    assert len(es) == 3                       # the 27 Jan sale is February's; a 0-baht tank loan books nothing


def test_route_customers_are_not_booked():
    from app import acc_sales
    assert acc_sales.is_route("สระบุรี", "58.เมืองสระบุรี-เฉลิมพระเกียรติ")
    assert not acc_sales.is_route("สระบุรี", "บริษัท ซีพี ออลล์ จํากัด (มหาช")
    assert not acc_sales.is_route("แก่งคอย", "58.เมืองสระบุรี")
    rows = [{"date": "2026-06-10", "product": "ซอง", "customer": c, "qty": 1, "unit": "กส", "weight": 20, "price": 100, "amount": 100}
            for c in ("58.เมืองสระบุรี-เฉลิมพระเกียรติ", "ร้านหนึ่ง")]
    out = acc_sales.build("สระบุรี", acc_sales.lines("สระบุรี", rows), ("2026-06", "2026-06"))
    assert len(out) == 1 and "ร้านหนึ่ง" in out[0]["source_id"]
