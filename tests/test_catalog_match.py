import pytest

from app import catalog
from app import purchasing as pu
from app import stock


def items_for(*specs):
    out = []
    for i, d in enumerate(specs):
        out.append({"id": str(i), "name": stock.compose_name(d["kind"], d["brand"], d["model"], d["spec"]), "aliases": d["words"]})
    return out


UCF = catalog.unit_item
ITEMS = items_for(UCF("UCF", 207), UCF("UCF", 207, 20), UCF("UCF", 208), UCF("UCF", 211, 32), UCF("UCFL", 208, 24), UCF("UCP", 209), catalog.groove_item("6204-2RS"),
                  catalog.belt_item("B", "29"), catalog.belt_item("B", "105"), catalog.belt_item("B", "10"), catalog.belt_item("C", "145"), catalog.belt_item("SPC", "3750Lw"))
CHAIN = {"id": "c", "name": "โซ่ KANA #80", "aliases": ["KANA #80", "KANA 80"]}


def hit(text):
    m = pu.match_item(text, [*ITEMS, CHAIN])
    return m["name"] if m else None


@pytest.mark.parametrize("text,name", [
    ("ลูกปืน SKF Y/UCF 207 ( FYJ 35 TF )", "ลูกปืนตุ๊กตาหน้าแปลนเหลี่ยม UCF207"),
    ("ลูกปืน SKF Y/UCF 207", "ลูกปืนตุ๊กตาหน้าแปลนเหลี่ยม UCF207"),
    ("F207 สี่เหลี่ยม", "ลูกปืนตุ๊กตาหน้าแปลนเหลี่ยม UCF207"),
    ("ลูกปืน SKF Y/UCF 207-20 ( FYJ 1.1/4 TF )", "ลูกปืนตุ๊กตาหน้าแปลนเหลี่ยม UCF207-20"),          # the inch bore is its own item, not the metric 207
    ("FYJ 1.1/4 TF", "ลูกปืนตุ๊กตาหน้าแปลนเหลี่ยม UCF207-20"),
    ("ตลับลูกปืน SKF Y/UCF 211-32 (FYJ 2 TF)", "ลูกปืนตุ๊กตาหน้าแปลนเหลี่ยม UCF211-32"),
    ('ลูกปืน SKF FYTB 1.1/2" TF', "ลูกปืนตุ๊กตาหน้าแปลนวงรี UCFL208-24"),                          # a quote mark after the size
    ("ลูกปืน SKF Y/UCP 209", "ลูกปืนตุ๊กตาขาตั้ง UCP209"), ("P209", "ลูกปืนตุ๊กตาขาตั้ง UCP209"),
    ("ลูกปืน SKF 6204-2RS1", "ลูกปืนเม็ดกลม 6204-2RS"),
    ("สายพานมิตซูโบชิ B29", "สายพาน B-29"), ("สายพาน B 29 Mitsubishi", "สายพาน B-29"), ("B-105", "สายพาน B-105"), ("สายพาน C145", "สายพาน C-145"),
    ("SPC3750Lw", "สายพาน SPC3750Lw"),
    ("โซ่ลูกปืน KANA #80-1R", "โซ่ KANA #80"),                                                  # a chain called "ลูกปืน" is a chain
])
def test_a_bill_line_finds_the_belt_or_bearing_by_whole_codes(text, name):
    assert hit(text) == name


@pytest.mark.parametrize("text", ["สายพาน B-12", "สายพาน B-1050", "ลูกปืน UCF 209", "ลูกปืน SKF 6205", "สายพาน D-29", "ทัสแบริ่ง ( THRUST BEARING SF )"])
def test_a_code_never_matches_inside_a_longer_code_and_unknown_parts_stay_unlinked(text):
    assert hit(text) is None


def test_the_shorter_code_is_still_found_when_it_is_the_whole_code():
    assert hit("สายพานมิตซูโบชิ B10") == "สายพาน B-10" and hit("B-105 x4") == "สายพาน B-105"


def test_belts_and_bearings_are_named_by_kind_and_size_not_by_brand():
    assert stock.compose_name("สายพาน", "Mitsuboshi", "B-29", "ร่อง B") == "สายพาน B-29"
    assert stock.compose_name("ลูกปืนตุ๊กตาหน้าแปลนเหลี่ยม", "SKF", "UCF207", "รู 35 มม.") == "ลูกปืนตุ๊กตาหน้าแปลนเหลี่ยม UCF207"
    assert stock.compose_name("แมกเนติก", "Fuji", "SC-N8", "220V") == "แมกเนติก Fuji SC-N8 220V"             # a contactor keeps its brand


def test_the_bore_is_explained_in_the_shop_words():
    assert catalog.bore(207, None)["text"] == "รูมิล 35 มม." and catalog.bore(208, None)["mm"] == 40 and catalog.bore(213, None)["mm"] == 65
    assert catalog.bore(207, 20)["text"] == "รูหุน 10 หุน (1¼ นิ้ว)" and catalog.bore(211, 32)["text"] == "รูหุน 16 หุน (2 นิ้ว)" and catalog.bore(213, 40)["text"] == "รูหุน 20 หุน (2½ นิ้ว)"
    assert catalog.bore(205, 16)["text"] == "รูหุน 8 หุน (1 นิ้ว)"
    u = UCF("UCF", 207, 20)
    assert u["remark"] == "รูหุน 10 หุน (1¼ นิ้ว) · F207 สี่เหลี่ยม · FYJ 1.1/4 TF" and "FYJ 1.1/4 TF" in u["words"] and u["unit"] == "ตลับ"
    assert catalog.belt_item("B", "29", ["เครื่องโม่", "ปั๊มลม"])["remark"] == "ร่อง B · ยาว 29 นิ้ว · ใช้กับ เครื่องโม่, ปั๊มลม"
    assert catalog.belt_item("SPC", "3750Lw")["model"] == "SPC3750Lw" and "3750 มม." in catalog.belt_item("SPC", "3750Lw")["remark"]


def test_the_brand_is_read_from_the_line_for_pricing_brand_by_brand():
    assert pu.detect_brand("สายพานมิตซูโบชิ B29") == "Mitsuboshi" and pu.detect_brand("สายพาน C148 Mitsubishi") == "Mitsuboshi"
    assert pu.detect_brand("ลูกปืน SKF Y/UCF 207") == "SKF" and pu.detect_brand("ลูกปืน NSK 6204") == "NSK"
    assert pu.detect_brand("แมกเนติก Mitsubishi S-T21") is None and pu.detect_brand("ปะเก็นฝาสูบ") is None and pu.detect_brand("ลูกปืน SKFX 1") is None


def test_prices_are_compared_brand_by_brand():
    rows = [{"bill_date": "2026-09-01", "vendor": "ร้าน ก", "brand": "SKF", "unit_cost": 600, "quantity": 2, "unit": "ตลับ"},
            {"bill_date": "2026-10-01", "vendor": "ร้าน ข", "brand": "NSK", "unit_cost": 520, "quantity": 1, "unit": "ตลับ"},
            {"bill_date": "2026-10-10", "vendor": "ร้าน ก", "brand": "SKF", "unit_cost": 640, "quantity": 1, "unit": "ตลับ"},
            {"bill_date": "2026-08-01", "vendor": "ร้าน ค", "brand": "SKF", "unit_cost": 580, "quantity": 1, "unit": "ตลับ"},
            {"bill_date": "2026-07-01", "vendor": "ร้าน ค", "brand": None, "unit_cost": 700, "quantity": 1, "unit": "ตลับ"}]
    s = pu.price_stats(rows)
    by = {b["brand"]: b for b in s["by_brand"]}
    assert [b["brand"] for b in s["by_brand"]] == ["NSK", "SKF", "ไม่ระบุยี่ห้อ"]                          # cheapest latest first
    assert by["SKF"]["last_cost"] == 640 and by["SKF"]["lowest_cost"] == 580 and by["SKF"]["lowest_vendor"] == "ร้าน ค" and by["SKF"]["times"] == 3
    assert s["last"]["brand"] == "SKF"


DISCS = [{"id": "g", "name": "ใบเจียร", "aliases": ["ใบเจียร", "แผ่นเจียร", "แผ่นเจียร์", "ใบเจียร์"]}, {"id": "t", "name": "ใบตัด", "aliases": ["ใบตัด", "แผ่นตัด"]}]


@pytest.mark.parametrize("text,name", [('แผ่นเจียร์หนา คลีนิค 4"', "ใบเจียร"), ('แผ่นตัดเหล็ก 4"', "ใบตัด"), ('แผ่นตัดเหล็กบาง คลีนิค 4"*1mm.', "ใบตัด"), ("ใบเจียร 4 นิ้ว", "ใบเจียร")])
def test_grinding_and_cutting_discs_by_their_bill_names(text, name):
    assert pu.match_item(text, DISCS)["name"] == name


def test_kana_chain_60_is_its_own_chain():
    chains = [CHAIN, {"id": "c6", "name": "โซ่ KANA #60", "aliases": ["KANA #60", "KANA 60"]}]
    assert pu.match_item("โซ่ลูกปืน KANA #60-1R", chains)["name"] == "โซ่ KANA #60"
    assert pu.match_item("โซ่ญี่ปุ่น KANA #80-1R", chains)["name"] == "โซ่ KANA #80"
