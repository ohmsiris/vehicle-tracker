import pytest
import re

from app import shops


def test_every_listed_contact_belongs_to_a_shop_that_is_offered():
    assert set(shops.DIRECTORY) <= set(shops.choices())


def test_phone_numbers_look_like_thai_mobile_numbers():
    for name, info in shops.DIRECTORY.items():
        for c in info["contacts"]:
            assert c["label"] and re.fullmatch(r"0\d{2}-\d{3}-\d{4}", c["phone"]), (name, c)


def test_the_numbers_the_owner_gave():
    d = shops.DIRECTORY
    assert d["ชมรมย์การช่าง"]["contacts"] == [{"label": "ช่างลม", "phone": "081-928-5474"}]
    assert d["24 ไดนาโม"]["contacts"][0]["phone"] == "081-374-0394"
    assert d[shops.IN_HOUSE]["contacts"][0]["phone"] == "085-967-4875"
    assert [c["phone"] for c in d["อิซูซุจึงกงเฮงแก่งคอย"]["contacts"]] == ["086-391-0548", "081-581-3465"]
    assert d["ฮีโน่ เอเซีย หนองแค"]["contacts"] == []                    # no number given yet


def test_a_name_on_a_bill_finds_the_same_shop_as_the_board():
    for name in shops.choices():                                  # an official name is its own canonical name
        assert shops.canonical(name) == name
    assert shops.canonical("24 ไดนาโม") == "24 ไดนาโม"    # not folded into มงคลไดนาโม
    assert shops.canonical("มงคลไดนาโม") == "มงคลไดนาโม"


def test_the_old_short_names_still_fold_into_the_official_ones():
    assert shops.canonical("อู่ชมรม") == "ชมรมย์การช่าง"
    assert shops.canonical("อู่ช่างลิ้ม") == "24 ไดนาโม" == shops.canonical("ช่างลิ้ม 24 ไดนาโม")
    assert shops.canonical("ศูนย์ Isuzu") == "อิซูซุจึงกงเฮงแก่งคอย"
    assert shops.canonical("ศูนย์ Hino") == "ฮีโน่ เอเซีย หนองแค"
    assert shops.canonical("ช่างเอ๋ (ในโรงงาน)") == "ซ่อมในโรงงาน" == shops.canonical("ช่างเอ๋")
    assert shops.canonical("อู่ช่างเก่ง") == "อู่ช่างเก่ง เจริญยนต์" == shops.canonical("ช่างเก่งเจริญยนต์")


# ------------------------------------------------- editing the shops (the settings page)
def other(name, aliases=()):
    return {"name": name, "aliases": list(aliases), "contacts": []}


def test_one_dealership_with_two_departments_is_one_shop():
    d = shops.DIRECTORY["อิซูซุจึงกงเฮงแก่งคอย"]
    assert [c["label"] for c in d["contacts"]] == ["ศูนย์บริการ", "ศูนย์ซ่อมสีและตัวถัง"]
    for written in ("บริษัท จึงกงเฮงอีซูซุ จำกัด (ศูนย์ซ่อมสีและตัวถัง แก่งคอย)", "อิซูซุจึงกงเฮง แก่งคอย ศูนย์บริการ", "ISUZU"):
        assert shops.canonical(written) == "อิซูซุจึงกงเฮงแก่งคอย"


def test_the_longest_word_wins():
    assert shops.canonical("ช่างลิ้ม 24 ไดนาโม") == "24 ไดนาโม" and shops.canonical("มงคล ไดนาโม") == "มงคลไดนาโม"
    custom = [{"name": "ไดนาโมใหญ่", "aliases": ["ไดนาโมใหญ่ลำลูกกา"], "active": True}, {"name": "ไดนาโม", "aliases": [], "active": True}]
    assert shops.canonical_in("ไดนาโมใหญ่ลำลูกกา", custom) == "ไดนาโมใหญ่" and shops.canonical_in("ไดนาโม", custom) == "ไดนาโม"


def test_a_switched_off_shop_still_groups_its_old_bills_but_is_not_offered():
    rows = [{"name": "A", "aliases": ["เอ"], "active": False, "in_house": False, "sort_order": 1}, {"name": "B", "aliases": [], "active": True, "in_house": False, "sort_order": 2}]
    assert shops.canonical_in("ร้านเอ", rows) == "A"
    saved = shops._snap["shops"]
    shops._snap.update(at=10**12, shops=rows)
    try:
        assert shops.choices() == ["B"] and list(shops.directory()) == ["B"]
        assert [p["label"] for p in shops.board_places()] == ["B"]
    finally:
        shops._snap.update(at=0.0, shops=saved)


def test_clean_shop_new_and_partial_edit():
    v = shops.clean_shop({"name": "  ร้านใหม่  ", "aliases": ["ใหม่", " ใหม่ ", "ร้านใหม่", ""], "contacts": [{"label": "ช่าง", "phone": "081-111-2222"}, {"label": "", "phone": ""}]}, others=[other("A")])
    assert v == {"name": "ร้านใหม่", "aliases": ["ใหม่"], "contacts": [{"label": "ช่าง", "phone": "081-111-2222"}]}          # own name / duplicates / blanks dropped
    assert shops.clean_shop({"active": False}, existing={"name": "A", "aliases": []}, others=[]) == {"active": False}


def test_clean_shop_refuses():
    for bad, message in (({"name": ""}, "ชื่อร้าน"), ({"name": "a"}, "มีร้านชื่อ"), ({"name": "ใหม่", "aliases": ["ชมรม"]}, "เป็นของร้าน"),
                         ({"name": "ใหม่", "aliases": "x"}, "ไม่ถูกต้อง"), ({"name": "ใหม่", "contacts": [{"label": "", "phone": "081-111-2222"}]}, "ชื่อแผนก"),
                         ({"name": "ใหม่", "contacts": [{"label": "x", "phone": "abc"}]}, "เบอร์โทร"), ({"name": "ใหม่", "sort_order": "x"}, "ตัวเลข")):
        with pytest.raises(shops.InvalidShop, match=message):
            shops.clean_shop(bad, others=[other("A"), other("B", ["ชมรม"])])
