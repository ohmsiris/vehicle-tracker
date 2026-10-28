from datetime import date

import pytest

from app import purchasing as pu
from app import stock as sk

TODAY = date(2026, 10, 23)
ITEM = "11111111-1111-1111-1111-111111111111"
VENDOR = "22222222-2222-2222-2222-222222222222"
MACHINE = "33333333-3333-3333-3333-333333333333"


def bill(**kw):
    base = {"bill_date": "2026-10-20", "vat_mode": "none", "lines": [{"description": "ลูกปืน 6205", "quantity": 2, "unit_price": 100}]}
    return {**base, **kw}


def clean(data):
    return pu.clean_bill(data, today=TODAY, vendors={VENDOR}, items={ITEM}, machines={MACHINE})


# ------------------------------------------------------------- what one unit really cost
def test_a_line_without_an_amount_gets_quantity_times_price():
    out = clean(bill())
    assert out["lines"][0]["line_total"] == 200 and out["bill"]["total"] == 200
    assert out["lines"][0]["unit_cost"] == 100 and out["lines"][0]["unit_cost_ex_vat"] == 100


def test_vat_added_at_the_bottom_is_part_of_the_cost():
    out = clean(bill(vat_mode="excluded", total=214))
    assert out["lines"][0]["unit_cost"] == pytest.approx(107)
    assert out["lines"][0]["unit_cost_ex_vat"] == pytest.approx(100)


def test_vat_already_in_the_price_is_taken_out_for_the_ex_vat_cost():
    out = clean(bill(vat_mode="included", total=200))
    assert out["lines"][0]["unit_cost"] == pytest.approx(100)
    assert out["lines"][0]["unit_cost_ex_vat"] == pytest.approx(93.4579, rel=1e-4)


def test_a_bill_discount_is_spread_over_the_lines():
    lines = [{"description": "a", "quantity": 1, "line_total": 300}, {"description": "b", "quantity": 2, "line_total": 100}]
    out = clean(bill(lines=lines, total=360))                  # 10 % off the whole bill
    assert [ln["unit_cost"] for ln in out["lines"]] == [pytest.approx(270), pytest.approx(45)]


def test_a_total_far_from_the_lines_is_not_trusted():
    costs = pu.line_costs([{"quantity": 1, "line_total": 100}], "none", 1000)   # a misread total, or half the bill typed
    assert costs[0]["unit_cost"] == 100


def test_no_quantity_means_no_unit_cost():
    assert pu.line_costs([{"quantity": None, "line_total": 50}], "none", 50)[0]["unit_cost"] is None


# ------------------------------------------------------------- what is refused
@pytest.mark.parametrize("data,msg", [
    (bill(bill_date="2569-10-20"), "พ.ศ."),
    (bill(bill_date="2027-01-01"), "อนาคต"),
    (bill(bill_date=""), "วันที่"),
    (bill(lines=[]), "ยังไม่มีรายการ"),
    (bill(lines=[{"description": "", "quantity": 1}]), "ชื่อรายการ"),
    (bill(lines=[{"description": "x", "quantity": 0}]), "จำนวน"),
    (bill(vat_mode="maybe"), "VAT"),
    (bill(vendor_id="44444444-4444-4444-4444-444444444444"), "ไม่พบร้าน"),
    (bill(branch="ลพบุรี"), "สาขา"),
])
def test_what_a_bill_may_not_be(data, msg):
    with pytest.raises(pu.InvalidPurchase, match=msg):
        clean(data)


def test_a_known_vendor_wins_over_a_new_one_and_a_new_item_replaces_a_link():
    out = clean(bill(vendor_id=VENDOR, new_vendor="ร้านใหม่", lines=[{"description": "สายพาน B-82", "quantity": 1, "line_total": 150,
                                                                       "item_id": ITEM, "new_item": {"name": "สายพาน B-82"}, "machine_id": MACHINE}]))
    assert out["new_vendor"] is None and out["bill"]["vendor_id"] == VENDOR
    ln = out["lines"][0]
    assert ln["item_id"] is None and ln["new_item"]["name"] == "สายพาน B-82" and ln["machine_id"] == MACHINE


def test_stock_is_not_received_unless_asked():
    assert clean(bill())["receive_stock"] is False
    assert clean(bill(receive_stock=True))["receive_stock"] is True


# ------------------------------------------------------------- finding the item and comparing prices
def test_a_bill_line_finds_its_item_by_the_items_words():
    items = [{"id": "a", "name": "ลูกปืน 6205-2RS", "aliases": ["6205"]}, {"id": "b", "name": "ลูกปืน 6205-2RS NSK", "aliases": []}]
    assert pu.match_item("ลูกปืน 6205-2RS NSK ญี่ปุ่น", items)["id"] == "b"       # the longest word wins
    assert pu.match_item("BRG 6205 zz", items)["id"] == "a"
    assert pu.match_item("สายพาน B-82", items) is None


def test_prices_show_the_last_the_lowest_and_each_supplier():
    rows = [
        {"bill_date": "2026-09-01", "vendor": "ร้าน ก", "unit_cost": 120, "unit_cost_ex_vat": 112.15, "quantity": 2, "unit": "ตัว"},
        {"bill_date": "2026-10-01", "vendor": "ร้าน ข", "unit_cost": 95, "unit_cost_ex_vat": 95, "quantity": 4, "unit": "ตัว"},
        {"bill_date": "2026-10-15", "vendor": "ร้าน ก", "unit_cost": 110, "unit_cost_ex_vat": 102.8, "quantity": 1, "unit": "ตัว"},
        {"bill_date": "2026-10-16", "vendor": "ร้าน ก", "unit_cost": None, "quantity": None},
    ]
    s = pu.price_stats(rows)
    assert s["count"] == 4 and s["last"]["unit_cost"] == 110 and s["lowest"]["vendor"] == "ร้าน ข"
    assert [v["vendor"] for v in s["by_vendor"]] == ["ร้าน ข", "ร้าน ก"]          # cheapest latest price first
    assert s["by_vendor"][1]["last_cost"] == 110 and s["by_vendor"][1]["lowest_cost"] == 110 and s["by_vendor"][1]["times"] == 2


def test_no_prices_yet():
    assert pu.price_stats([])["last"] is None


# ------------------------------------------------------------- the stock (draft)
def test_on_hand_is_the_sum_of_the_moves():
    moves = [{"item_id": "a", "qty": 10}, {"item_id": "a", "qty": -3}, {"item_id": "b", "qty": 1}, {"item_id": "a", "qty": "-2"}]
    assert sk.on_hand(moves) == {"a": 5, "b": 1}


def test_a_stock_take_writes_the_difference():
    m = sk.clean_move({"kind": "count", "item_id": ITEM, "qty": 7}, today=TODAY, items={ITEM}, machines=set(), on_hand_now=10)
    assert m["qty"] == -3 and m["kind"] == "count"


def test_taking_out_is_negative_and_needs_a_quantity():
    m = sk.clean_move({"kind": "out", "item_id": ITEM, "qty": "2", "machine_id": MACHINE}, today=TODAY, items={ITEM}, machines={MACHINE})
    assert m["qty"] == -2 and m["machine_id"] == MACHINE
    with pytest.raises(sk.InvalidStock):
        sk.clean_move({"kind": "out", "item_id": ITEM, "qty": 0}, today=TODAY, items={ITEM}, machines=set())
    with pytest.raises(sk.InvalidStock):
        sk.clean_move({"kind": "steal", "item_id": ITEM, "qty": 1}, today=TODAY, items={ITEM}, machines=set())


def test_item_names_and_words_belong_to_one_item():
    others = [{"name": "ลูกปืน 6205", "aliases": ["6205"]}]
    with pytest.raises(sk.InvalidStock, match="มีรายการชื่อ"):
        sk.clean_item({"name": "ลูกปืน 6205"}, others=others)
    with pytest.raises(sk.InvalidStock, match="เป็นของ"):
        sk.clean_item({"name": "ลูกปืน 6206", "aliases": ["6205"]}, others=others)
    out = sk.clean_item({"name": " สายพาน  B-82 ", "unit": "เส้น", "min_qty": "2", "aliases": ["B-82", "b-82", ""]}, others=others)
    assert out["name"] == "สายพาน B-82" and out["min_qty"] == 2 and out["aliases"] == ["B-82"]


def test_a_bill_photographed_again_is_caught_by_supplier_and_bill_number_or_date_and_total():
    saved = [{"id": "b1", "vendor_id": "v1", "vendor_name": "บริษัท ก", "bill_date": "2026-10-21", "bill_no": "IV-0042", "total": 1134.2},
             {"id": "b2", "vendor_id": "v2", "vendor_name": "บริษัท ข", "bill_date": "2026-10-21", "bill_no": "IV-0042", "total": 500}]
    same = pu.same_bill_matches({"vendor_id": "v1", "vendor_name": "บริษัท ก", "bill_date": "2026-10-21", "bill_no": "iv 0042", "total": 99}, saved)
    assert [(m["bill_id"], m["reason"]) for m in same] == [("b1", "เลขที่บิลเดียวกัน")]                      # spacing and capitals in the number do not matter
    by_total = pu.same_bill_matches({"vendor_id": "v1", "vendor_name": "บริษัท ก", "bill_date": "2026-10-21", "bill_no": None, "total": 1134.2}, saved)
    assert [(m["bill_id"], m["reason"]) for m in by_total] == [("b1", "วันที่และยอดรวมเท่ากัน")]
    assert pu.same_bill_matches({"vendor_id": "v3", "vendor_name": "บริษัท ค", "bill_date": "2026-10-21", "bill_no": "IV-0042", "total": 1134.2}, saved) == []     # another supplier
    assert pu.same_bill_matches({"vendor_id": "v1", "bill_date": "2026-10-22", "bill_no": None, "total": 1134.2}, saved) == []                                          # another day
    by_name = pu.same_bill_matches({"vendor_id": None, "vendor_name": "บริษัท  ก", "bill_date": "2026-10-21", "bill_no": "IV-0042", "total": None},
                                           [{**saved[0], "vendor_id": None}])
    assert len(by_name) == 1                                                                                   # a supplier not in the list yet is found by its name


# ------------------------------------------------------------- what a bill is: stock / a machine's repair / a reference parts list
def test_a_bill_is_for_stock_by_default_and_a_repair_or_reference_bill_needs_its_machine_or_model():
    out = clean(bill(receive_stock=True))
    assert out["purpose"] == "stock" and out["machine_id"] is None and out["machine_model"] is None and out["receive_stock"] is True
    rep = clean(bill(purpose="repair", machine_id=MACHINE, receive_stock=True, machine_model="ignored"))
    assert rep["purpose"] == "repair" and rep["machine_id"] == MACHINE and rep["machine_model"] is None
    assert rep["receive_stock"] is False and rep["lines"][0]["machine_id"] == MACHINE          # a repair's parts go into the machine, not the store
    ref = clean(bill(purpose="reference", machine_model=" MYCOM 8WB-HE ", machine_id=MACHINE, receive_stock=True))
    assert ref["purpose"] == "reference" and ref["machine_model"] == "MYCOM 8WB-HE" and ref["machine_id"] is None and ref["receive_stock"] is False
    for data, frag in ((bill(purpose="repair"), "เลือกเครื่อง"), (bill(purpose="reference"), "เลือกรุ่น"), (bill(purpose="gift"), "คืออะไร")):
        with pytest.raises(pu.InvalidPurchase, match=frag):
            clean(data)


def test_a_reference_parts_list_belongs_to_every_machine_of_that_model():
    m = {"id": "m1", "model": "8WB-HE", "make": "MYCOM"}
    assert pu.model_matches(m, "MYCOM 8WB-HE") and pu.model_matches(m, "mycom 8wbhe") and not pu.model_matches(m, "SMC108L")
    assert not pu.model_matches({"id": "m2", "model": None}, "MYCOM 8WB-HE")
    assert pu.machine_models([m, {"model": "8WB-HE", "make": "MYCOM"}, {"model": "SMC108L", "make": None}, {"model": None}]) == ["MYCOM 8WB-HE", "SMC108L"]
