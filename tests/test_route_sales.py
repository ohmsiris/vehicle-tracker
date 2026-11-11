from app import route_sales as rs


def test_sheet_rows_become_route_and_customers_with_prices():
    rows = [["รถเบอร์", "35", "พนักงานขับรถ", "ปรีชา คำแดงใส", "วันที่", "...."],
            ["สาย", "หนองแค - วังน้อย", "เบอร์โทรศัพท์", "063-198-2104", "", ""],
            ["ลูกค้า", "ถัง", "ราคา", "ซอง", "กั๊ก", "หลอด", "เล็ก", "แพ็ค", "โม่"],
            ["1. ร้านขายส่ง (ตลาดหนองแค)", "V200-1805 / V850-241", "", "150", "", "30", "30", "", "30"],
            ["2. ตลาดหนองแค", "V200-1262", "35", "", "", "", "", "", ""],
            ["55. เซียงกง (หินกอง)", "T200-175", "", "", "", "40", "40", "90", "40"],
            ["66. Big C Mini หนองปลาหมอ (รหัสสาขา 21525)", "", "119", "", "", "", "", "", ""],
            ["21. ไก่", "", "150", "", "", "", "", "", ""],
            ["รถเบอร์", "35", "พนักงานขับรถ", "ปรีชา คำแดงใส"],                     # the header again on the next page
            ["2. ตลาดหนองแค", "V200-1262", "35"],                                   # a repeated row is not a second customer
            ["รวมถุง", "", ""]]
    s = rs.parse_sheet(rows)
    assert (s["truck_no"], s["driver_name"], s["name"], s["driver_phone"]) == ("35", "ปรีชา คำแดงใส", "หนองแค - วังน้อย", "063-198-2104")
    c = {x["seq"]: x for x in s["customers"]}
    assert [x["seq"] for x in s["customers"]] == [1, 2, 21, 55, 66]
    assert c[1]["prices"] == {"zong": 150, "lod": 30, "lek": 30, "mo": 30} and c[1]["bins"] == "V200-1805, V850-241"
    assert c[2]["prices"] == {"lod": 35, "lek": 35, "mo": 35} and c[2]["billing"] == "cash"
    assert c[55]["prices"]["pack"] == 90
    assert c[66]["prices"] == {"pack": 119} and c[66]["billing"] == "invoice"
    assert c[21]["prices"] == {"zong": 150}


def test_day_totals_cash_credit_invoice_returns_and_cash_count():
    customers = {"1": {"prices": {"lod": 40, "mo": 40}, "billing": "cash"}, "2": {"prices": {"lod": 35}, "billing": "cash"},
                 "3": {"prices": {"pack": 100}, "billing": "invoice"}}
    doc = {"loads": [{"q": {"lod": 30, "pack": 5}}, {"q": {"lod": 10, "mo": 2}}],
           "sales": {"1": {"q": {"lod": 3, "mo": 1}, "paid": 160},                 # cash 160
                     "2": {"q": {"lod": 2}, "paid": 0, "old_paid": 100},           # 70 on credit, 100 of old debt paid
                     "3": {"q": {"pack": 4}},                                       # วางบิล 400, no cash
                     "9": {"q": {"lod": 1}}},                                       # unknown customer: no price -> 0
           "returns": {"lod": 33, "pack": 1, "mo": 1},
           "cash": {"100": 2, "50": 1, "20": 0, "coins": 10},                      # 260 counted, 260 expected
           "km_start": 111080, "km_end": 111220}
    t = rs.compute(doc, customers)
    assert t["loaded"]["lod"] == 40 and t["sold"]["lod"] == 6 and t["left"]["lod"] == 34
    assert t["diff"]["lod"] == -1 and t["diff"]["pack"] == 0 and t["diff"]["mo"] == 0
    assert (t["amount"], t["cash_sales"], t["credit"], t["invoiced"], t["old_paid"]) == (630, 160, 70, 400, 100)
    assert t["expected_cash"] == 260 and t["counted"] == 260 and t["km"] == 140
    assert t["flags"][0] == "ใหญ่ ขาด 1" and "ไม่มีราคา" in t["flags"][1] and len(t["flags"]) == 2


def test_paid_never_above_the_amount_and_pin_rules():
    t = rs.compute({"sales": {"1": {"q": {"lod": 1}, "paid": 999}}}, {"1": {"prices": {"lod": 40}}})
    assert t["cash_sales"] == 40 and t["credit"] == 0
    assert rs.valid_pin("0427") and not rs.valid_pin("427") and not rs.valid_pin("04a7")
    salt = "00" * 16
    assert rs.hash_pin("1234", salt) == rs.hash_pin("1234", salt) != rs.hash_pin("1235", salt)


def test_only_today_or_an_unsent_yesterday_before_noon_can_change_and_a_sent_day_is_locked():
    from datetime import date, datetime
    now = datetime(2026, 11, 10, 9, 30)
    t, y = date(2026, 11, 10), date(2026, 11, 9)
    assert rs.edit_refusal(t, None, now) is None and rs.edit_refusal(t, "open", now) is None
    assert rs.edit_refusal(y, "open", now) is None                                   # finish yesterday before noon
    assert "เที่ยง" in rs.edit_refusal(y, "open", datetime(2026, 11, 10, 12, 0))
    assert rs.edit_refusal(date(2026, 11, 8), None, now) == "แก้ได้เฉพาะใบขายวันนี้"
    assert "ล็อก" in rs.edit_refusal(t, "closed", now) and "ล็อก" in rs.edit_refusal(t, "checked", now)


def test_free_customers_discounts_returns_from_the_plant_and_km_from_the_last_reading():
    customers = {"1": {"prices": {}, "billing": "cash", "free": True},                     # ตำรวจ: always free
                 "2": {"prices": {"lod": 40}, "billing": "cash"}}
    doc = {"loads": [{"q": {"lod": 20}}],
           "sales": {"1": {"q": {"lod": 2}},
                     "2": {"q": {"lod": 5}, "discount": 20, "paid": 180}},             # 200 - 20 discount = 180 cash
           "km_end": 111220}
    t = rs.compute(doc, customers, returns={"lod": 12}, km_start=111080)
    assert t["sold"]["lod"] == 7 and t["free"]["lod"] == 2 and t["left"]["lod"] == 13
    assert t["amount"] == 180 and t["cash_sales"] == 180 and t["discount"] == 20 and t["credit"] == 0
    assert t["returned"]["lod"] == 12 and t["diff"]["lod"] == -1 and t["km"] == 140
    assert "ใหญ่ ขาด 1" in t["flags"] and any("อื่นๆ 20" in f for f in t["flags"])
    assert t["commission_sales"] == 180                                                  # a discount for another reason is the driver's
    one_off = rs.compute({"sales": {"2": {"q": {"lod": 1}, "free": True}}}, customers)
    assert one_off["amount"] == 0 and one_off["cash_sales"] == 0 and one_off["commission_sales"] == 0


def test_the_plants_fault_does_not_cut_the_drivers_commission():
    customers = {"1": {"prices": {}, "free": True}, "2": {"prices": {"lod": 40, "zong": 150}}, "3": {"prices": {"lod": 40}}}
    doc = {"sales": {"1": {"q": {"lod": 2}},                                            # ตำรวจ: not a sale, not counted
                     "2": {"q": {"lod": 5}, "discount": 40, "reason": "factory"},       # 200 - 40 for broken bags
                     "3": {"q": {"zong": 0, "lod": 2}, "free": True, "reason": "factory"}}}   # 80 given free for bad ice
    t = rs.compute(doc, customers)
    assert t["amount"] == 160 and t["cash_sales"] == 160
    assert t["factory_waived"] == 120 and t["commission_sales"] == 280
    assert t["sold"]["lod"] == 9 and any("โรงงาน" in f for f in t["flags"])
    line = [x for x in t["lines"] if x["customer"] == "3"][0]
    assert line["waived"] == 80 and line["waived_reason"] == "factory"


def test_sheet_free_customer():
    c = rs.customer_from_row(["1. ตำรวจ", "", "ฟรี", "", "", "", "", "", ""])
    assert c["free"] is True and c["prices"] == {}


def test_the_phone_cannot_change_a_price_and_an_unpriced_product_is_flagged():
    customers = {"1": {"name": "ร้านเอ", "prices": {"lod": 40}}}
    t = rs.compute({"sales": {"1": {"q": {"lod": 2, "pack": 1}, "price": {"lod": 1}}}}, customers)
    assert t["amount"] == 80                                             # 2 x 40: the phone's price of 1 is ignored
    assert any("ร้านเอ" in f and "แพ็ค" in f for f in t["flags"])


def test_a_day_never_sent_is_unsent_once_it_can_no_longer_be_sent():
    from datetime import date, datetime
    now = datetime(2026, 11, 10, 9, 0)
    assert not rs.unsent(date(2026, 11, 10), "open", now)                 # today: still selling
    assert not rs.unsent(date(2026, 11, 9), "open", now)                  # yesterday before noon: can still send
    assert rs.unsent(date(2026, 11, 9), "open", datetime(2026, 11, 10, 13, 0))
    assert rs.unsent(date(2026, 11, 7), "open", now)
    assert not rs.unsent(date(2026, 11, 7), "closed", now)


def test_phone_label():
    assert rs.phone_label("Mozilla/5.0 (Linux; Android 13; K) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36") == "Android · Chrome"
    assert rs.phone_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1 Line/13.0.0") == "iPhone · LINE"
    assert rs.phone_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile/15E148 Safari/604.1") == "iPhone · Safari"
    assert rs.phone_label("") == "เครื่องอื่น"


def test_a_shop_the_driver_added_is_cash_only_and_flagged():
    doc = {"new_customers": [{"key": "new:1", "name": " ร้าน  ใหม่ ", "phone": "081-234 5678x", "prices": {"lod": 40, "bad": 9}}],
           "sales": {"new:1": {"q": {"lod": 2}, "paid": 0}}}
    walk = rs.walkin_customers(doc)
    assert walk["new:1"]["name"] == "ร้าน ใหม่" and walk["new:1"]["prices"] == {"lod": 40.0} and walk["new:1"]["phone"] == "081-234 5678"
    t = rs.compute(doc, walk)
    assert t["amount"] == 80 and t["cash_sales"] == 80 and t["credit"] == 0       # no credit for a shop not on the list
    assert t["walkins"] == [{"key": "new:1", "name": "ร้าน ใหม่", "phone": "081-234 5678", "amount": 80.0}]
    assert any(f.startswith("ลูกค้าเพิ่มเอง: ร้าน ใหม่") for f in t["flags"])
    assert rs._line_who("new:1", walk) == {"customer_id": None, "walkin": "ร้าน ใหม่"}
    assert rs._line_who("12", walk) == {"customer_id": 12, "walkin": None}


def test_clean_walkins_drops_junk():
    assert rs.clean_walkins([{"key": "x", "name": "a"}, {"key": "new:2", "name": ""}, "junk", {"key": "new:3", "name": "b"},
                             {"key": "new:3", "name": "dup"}]) == [{"key": "new:3", "name": "b", "phone": "", "prices": {}}]
    assert rs.clean_walkins(None) == []


def test_the_office_sheet_is_tidied_to_the_phone_shape():
    d = rs.clean_doc({"sales": {"5": {"q": {"lod": "2", "bad": 3, "lek": 0}, "paid": "70", "old_paid": ""}, "6": {"q": {}}, "7": "junk"},
                      "loads": [{"q": {"lod": 10}}, {"q": {}}], "cash": {"100": "3", "7": 1, "coins": 2}, "km_end": "1200"})
    assert d["sales"] == {"5": {"q": {"lod": 2.0}, "paid": 70.0}}
    assert d["loads"] == [{"q": {"lod": 10.0}}] and d["cash"] == {"100": 3.0, "coins": 2.0} and d["km_end"] == 1200.0
    t = rs.compute(d, {"5": {"prices": {"lod": 40}}})
    assert t["amount"] == 80 and t["cash_sales"] == 70 and t["credit"] == 10


def test_an_order_keeps_only_real_products_and_the_phone_orders_today_or_tomorrow(monkeypatch):
    from datetime import date
    import pytest
    assert rs.clean_order({"lod": "90", "lek": 0, "bad": 5, "mo": "x"}) == {"lod": 90.0}
    monkeypatch.setattr(rs, "today", lambda: date(2026, 11, 11))
    saved = []
    monkeypatch.setattr(rs, "save_order", lambda route_id, day, q, by: saved.append((route_id, day, q, by)) or {"order": q})
    rs.driver_order({"route_id": 30, "name": "ทดสอบ"}, "2026-11-12", {"lod": 5})
    assert saved == [(30, date(2026, 11, 12), {"lod": 5}, "ทดสอบ")]
    with pytest.raises(rs.RouteSalesError):
        rs.driver_order({"route_id": 30, "name": "x"}, "2026-11-14", {"lod": 5})


def test_the_office_can_set_a_line_total():
    customers = {"1": {"name": "ร้าน", "prices": {"lod": 50}}}
    t = rs.compute({"sales": {"1": {"q": {"lod": 1, "lek": 4}}}}, customers)
    assert t["amount"] == 50 and any("ไม่มีราคา" in f for f in t["flags"])
    t = rs.compute({"sales": {"1": {"q": {"lod": 1, "lek": 4}, "office_amount": 250}}}, customers)
    assert t["amount"] == 250 and not any("ไม่มีราคา" in f for f in t["flags"])
    assert rs.clean_doc({"sales": {"1": {"q": {"lod": 1}, "office_amount": "250"}}})["sales"]["1"]["office_amount"] == 250
