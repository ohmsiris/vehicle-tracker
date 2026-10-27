from datetime import date

import pytest

from app import stock
from app.purchasing import match_item

TODAY = date(2026, 10, 27)
I1, I2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def test_the_standard_name_is_kind_brand_model_spec():
    assert stock.compose_name("แมกเนติก", "Fuji", "SC-N8", "220V") == "แมกเนติก Fuji SC-N8 220V"
    assert stock.compose_name("เทปพันสายไฟ", None, None, None) == "เทปพันสายไฟ"
    assert stock.compose_name(" สายไฟ ", "", "", "1×2.5") == "สายไฟ 1×2.5"


def test_an_item_with_a_kind_gets_its_standard_name_and_its_model_as_a_bill_word():
    v = stock.clean_item({"kind": "โอเวอร์โหลด", "brand": "Mitsubishi", "model": "TH-T25", "spec": "18-26A", "unit": "ตัว"}, others=[])
    assert v["name"] == "โอเวอร์โหลด Mitsubishi TH-T25 18-26A" and v["aliases"] == ["TH-T25"]
    taken = [{"name": "x", "aliases": ["TH-T25"]}]
    assert "aliases" not in stock.clean_item({"kind": "โอเวอร์โหลด", "model": "TH-T25", "spec": "7-11A"}, others=taken)        # a word already used: no silent steal


def test_a_typed_name_wins_and_an_item_without_a_kind_keeps_its_name():
    assert stock.clean_item({"kind": "ลูกลอย", "model": "UKY-2", "name": "ลูกลอยบ่อน้ำ"}, others=[])["name"] == "ลูกลอยบ่อน้ำ"
    assert stock.clean_item({"name": "ปะเก็นฝาสูบ"}, others=[])["name"] == "ปะเก็นฝาสูบ"
    with pytest.raises(stock.InvalidStock, match="ใส่ชื่อ"):
        stock.clean_item({"brand": "Fuji"}, others=[])


def test_changing_a_part_renames_a_standard_item_and_names_stay_unique():
    existing = {"id": I1, "name": "แมกเนติก Fuji SC-N2 220V", "kind": "แมกเนติก", "brand": "Fuji", "model": "SC-N2", "spec": "220V"}
    assert stock.clean_item({"spec": "24V"}, existing=existing, others=[])["name"] == "แมกเนติก Fuji SC-N2 24V"
    assert "name" not in stock.clean_item({"min_qty": 2}, existing=existing, others=[])                      # nothing about the name changed
    with pytest.raises(stock.InvalidStock, match="อยู่แล้ว"):
        stock.clean_item({"spec": "24V"}, existing=existing, others=[{"name": "แมกเนติก fuji sc-n2 24v", "aliases": []}])


def test_bill_wording_finds_the_item_whatever_the_spacing_dashes_and_capitals():
    items = [{"id": I1, "name": "แมกเนติก Fuji SC-N2 220V", "aliases": ["SC-N2"]},
             {"id": I2, "name": "โอเวอร์โหลด Fuji TR-N2/3 12-18A", "aliases": ["TR-N2/3+12-18A"]},
             {"id": "3", "name": "โอเวอร์โหลด Fuji TR-N2/3 6-9A", "aliases": ["TR-N2/3+6-9A"]}]
    for text in ("Magnetic SCN2 220V", "sc n2", "แมกเนติก SC-N2"):
        assert match_item(text, items)["id"] == I1
    assert match_item("TR-N2/3 12-18A (Fuji)", items)["id"] == I2 and match_item("TR-N2/3 6-9A", items)["id"] == "3"
    assert match_item("TR-N2/3", items) is None                                                              # the rating is needed to tell them apart


def test_the_weekly_list_is_all_or_nothing_and_blank_rows_are_skipped():
    ok = {"kind": "out", "move_date": "2026-10-26", "notes": "สัปดาห์ 20-26",
          "rows": [{"item_id": I1, "qty": "2"}, {"item_id": "", "qty": ""}, {"item_id": I2, "qty": "1.5", "machine_id": ""}]}
    moves = stock.clean_batch(ok, today=TODAY, items={I1, I2}, machines=set())
    assert [(m["item_id"], m["qty"], m["kind"], m["move_date"], m["notes"]) for m in moves] == [(I1, -2.0, "out", "2026-10-26", "สัปดาห์ 20-26"), (I2, -1.5, "out", "2026-10-26", "สัปดาห์ 20-26")]
    for rows, frag in (([{"item_id": I1, "qty": "2"}, {"item_id": I2, "qty": "abc"}], "แถวที่ 2"), ([{"item_id": "", "qty": "3"}], "แถวที่ 1"),
                       ([{"item_id": I1, "qty": "0"}], "แถวที่ 1"), ([{"item_id": "", "qty": ""}], "ไม่มีรายการ"), ([{"item_id": "99999999-9999-9999-9999-999999999999", "qty": "1"}], "ไม่พบรายการ")):
        with pytest.raises(stock.InvalidStock, match=frag):
            stock.clean_batch({"kind": "out", "rows": rows}, today=TODAY, items={I1, I2}, machines=set())
    with pytest.raises(stock.InvalidStock, match="รับเข้าหรือเบิก"):
        stock.clean_batch({"kind": "count", "rows": [{"item_id": I1, "qty": 1}]}, today=TODAY, items={I1}, machines=set())
