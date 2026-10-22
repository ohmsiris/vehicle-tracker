import re

from app import shops


def test_every_listed_contact_belongs_to_a_shop_that_is_offered():
    assert set(shops.DIRECTORY) <= set(shops.choices())


def test_phone_numbers_look_like_thai_mobile_numbers():
    for name, info in shops.DIRECTORY.items():
        assert info["full_name"]
        for c in info["contacts"]:
            assert c["label"] and re.fullmatch(r"0\d{2}-\d{3}-\d{4}", c["phone"]), (name, c)


def test_the_numbers_the_owner_gave():
    d = shops.DIRECTORY
    assert d["อู่ชมรม"]["contacts"] == [{"label": "ช่างลม", "phone": "081-928-5474"}]
    assert d["อู่ช่างลิ้ม"]["contacts"][0]["phone"] == "081-374-0394"
    assert d[shops.IN_HOUSE]["contacts"][0]["phone"] == "085-967-4875"
    assert [c["phone"] for c in d["ศูนย์ Isuzu"]["contacts"]] == ["086-391-0548", "081-581-3465"]
    assert d["ศูนย์ Hino"]["contacts"] == []                    # no number given yet


def test_a_name_on_a_bill_finds_the_same_shop_as_the_board():
    assert shops.canonical("ช่างลิ้ม 24 ไดนาโม") == "อู่ช่างลิ้ม"
    assert shops.canonical("ชมรมย์การช่าง") == "อู่ชมรม"
    assert shops.canonical("อิซูซุจึงกงเฮงแก่งคอย") == "ศูนย์ Isuzu"
    assert shops.canonical("ฮีโน่ เอเซีย หนองแค") == "ศูนย์ Hino"
