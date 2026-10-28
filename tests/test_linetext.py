import pytest

from app import service_plan as sp
from app.linetext import strip_type_word


@pytest.mark.parametrize("text,clean,word", [
    ("อะไหล่ ไส้กรองอากาศ", "ไส้กรองอากาศ", "อะไหล่"), ("ค่าแรง เปลี่ยนซีลลูกปืน และ/หรือ จาระบีลูกปืนล้อหลัง", "เปลี่ยนซีลลูกปืน และ/หรือ จาระบีลูกปืนล้อหลัง", "ค่าแรง"),
    ("  วัสดุสิ้นเปลือง : น้ำมันล้างชิ้นส่วน", "น้ำมันล้างชิ้นส่วน", "วัสดุสิ้นเปลือง"),
    ("ค่าแรงเปลี่ยนน้ำมันเครื่อง", "ค่าแรงเปลี่ยนน้ำมันเครื่อง", None),          # one written word: left alone
    ("ค่าแรง", "ค่าแรง", None), ("ไส้กรองอากาศ", "ไส้กรองอากาศ", None), ("", "", None), (None, "", None),
])
def test_the_type_word_in_front_of_a_line_is_taken_off(text, clean, word):
    assert strip_type_word(text) == (clean, word)


def test_the_plan_sees_through_a_type_word_already_saved_in_front():
    assert sp.classify_line("อะไหล่ ไส้กรองอากาศ") == "air_filter" and sp.classify_line("ค่าแรง เปลี่ยนน้ำมันเครื่อง") == "engine_oil"
