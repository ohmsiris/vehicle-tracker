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
    assert t["flags"] == ["หลอด ขาด 1"]


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
    assert "หลอด ขาด 1" in t["flags"] and any("ส่วนลด 20" in f for f in t["flags"])
    one_off = rs.compute({"sales": {"2": {"q": {"lod": 1}, "free": True}}}, customers)
    assert one_off["amount"] == 0 and one_off["cash_sales"] == 0 and any("แจกฟรีนอกรายชื่อ" in f for f in one_off["flags"])


def test_sheet_free_customer():
    c = rs.customer_from_row(["1. ตำรวจ", "", "ฟรี", "", "", "", "", "", ""])
    assert c["free"] is True and c["prices"] == {}
