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
    assert d["ช่างลิ้ม 24 ไดนาโม"]["contacts"][0]["phone"] == "081-374-0394"
    assert d[shops.IN_HOUSE]["contacts"][0]["phone"] == "085-967-4875"
    assert [c["phone"] for c in d["อิซูซุจึงกงเฮงแก่งคอย"]["contacts"]] == ["086-391-0548", "081-581-3465"]
    assert d["ฮีโน่ เอเซีย หนองแค"]["contacts"] == []                    # no number given yet


def test_a_name_on_a_bill_finds_the_same_shop_as_the_board():
    for name in shops.choices():                                  # an official name is its own canonical name
        assert shops.canonical(name) == name
    assert shops.canonical("ช่างลิ้ม 24 ไดนาโม") == "ช่างลิ้ม 24 ไดนาโม"    # not folded into มงคลไดนาโม
    assert shops.canonical("มงคลไดนาโม") == "มงคลไดนาโม"


def test_the_old_short_names_still_fold_into_the_official_ones():
    assert shops.canonical("อู่ชมรม") == "ชมรมย์การช่าง"
    assert shops.canonical("อู่ช่างลิ้ม") == "ช่างลิ้ม 24 ไดนาโม"
    assert shops.canonical("ศูนย์ Isuzu") == "อิซูซุจึงกงเฮงแก่งคอย"
    assert shops.canonical("ศูนย์ Hino") == "ฮีโน่ เอเซีย หนองแค"
    assert shops.canonical("อู่ช่างเก่ง") == "อู่ช่างเก่ง เจริญยนต์" == shops.canonical("ช่างเก่งเจริญยนต์")
