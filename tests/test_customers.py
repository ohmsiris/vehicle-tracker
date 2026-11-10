"""The customer register: reading the sheet and linking rows to route customers (app/customers.py)."""
from app import customers as cu

TRUCK = {1: "35", 2: "20"}
CUSTS = [{"id": 10, "route_id": 1, "seq": 1, "name": "ร้านขายส่ง (ตลาด)", "bins": "V200-1805, V850-241"},
         {"id": 11, "route_id": 1, "seq": 2, "name": "ไก่ย่าง (โพนทอง)", "bins": "V200-2260"},
         {"id": 12, "route_id": 1, "seq": 3, "name": "ร้านลูกชิ้นนิลวรรณ", "bins": "V200-2260"},
         {"id": 13, "route_id": 1, "seq": 4, "name": "หมูจุ่ม", "bins": None},
         {"id": 20, "route_id": 2, "seq": 1, "name": "ร้านขายของชำ เฮงเฮง", "bins": "V200-2109"},
         {"id": 21, "route_id": 2, "seq": 2, "name": "ส้มตำ", "bins": "S200-0412"}]


def reg(truck, bins, shop="บุคคล", contact="สมชาย"):
    return {"truck_no": truck, "bins": [{"size": s, "no": n} for s, n in bins], "shop": shop, "contact_name": contact}


def test_reading_numbers_and_dates():
    assert cu.norm_no(6.0) == "6" and cu.norm_no("0412") == "412" and cu.norm_no(None) == ""
    assert cu.bin_tokens("V200-1805, S200-0412") == {("200", "1805"), ("200", "412")}
    assert cu.be_date("20/09/2568") == "2025-09-20" and cu.be_date("x") is None
    rows = cu.register_rows([["head"], [1.0, "14N", "คนขับ", 6.0, "20/09/2568", 200, 1902, None, None, None, None, None, None, False,
                                        "บุคคล", "ชื่อ", "081", "1-2345", None]])
    assert rows[0]["cust_no"] == 1 and rows[0]["truck_no"] == "6" and rows[0]["bins"] == [{"size": "200", "no": "1902"}]


def test_linking_rules():
    assert cu.match(reg("35", [("200", "1805")]), CUSTS, TRUCK) == {"customer_id": 10, "match": "truck+bin", "suggest": []}
    m = cu.match(reg("35", [("200", "2260")], shop="ลูกชิ้น นิลวรรณ"), CUSTS, TRUCK)                 # two share the bin: the name decides
    assert m["customer_id"] == 12 and m["match"] == "truck+bin+name"
    assert cu.match(reg("35", [("200", "9999")], shop="หมูจุ่ม (ดาวเรือง)"), CUSTS, TRUCK)["customer_id"] == 13
    m = cu.match(reg("15", [("200", "2109")], shop="ร้านเฮงเฮง ขายของชำ"), CUSTS, TRUCK)             # moved truck, same shop
    assert m["customer_id"] == 20 and m["match"] == "bin+name"
    m = cu.match(reg("35", [("200", "412")], shop="อู่ซ่อมรถ"), CUSTS, TRUCK)                         # bin elsewhere, other shop: never guessed
    assert m["customer_id"] is None and m["suggest"] == [21]
    m = cu.match(reg("35", [("200", "2260")]), CUSTS, TRUCK)                                       # two share the bin, no name: review
    assert m["customer_id"] is None and set(m["suggest"]) == {11, 12}


def test_names_that_do_not_agree_are_checked():
    assert cu.name_ok({"shop": "ป้าแน๊ต"}, "ป้าแน็ต")                          # spelling only
    assert not cu.name_ok({"shop": "ขายเนื้อ"}, "โรงอาหาร")                    # the bin may have moved
    assert not cu.name_ok({"shop": "บุคคล", "contact_name": "สมชาย ใจดี"}, "ข้าวแกง")   # only a person's name: look
