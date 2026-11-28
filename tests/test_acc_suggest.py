"""The account of a retyped check-page line from its wording (owner, 28 Nov 2026)."""
from collections import Counter

from app import acc_suggest as a


def test_wording_is_compared_without_numbers_brackets_or_the_unsure_mark():
    assert a.norm("เติมน้ำมัน 84-4764 (34) (อ่านไม่ชัด)") == "เติมน้ำมัน"
    assert a.norm("ปะยางรถเบอร์ 21") == "ปะยางรถเบอร์"


def test_the_books_answer_first_then_the_closest_wording():
    past = {"เติมน้ำมัน": Counter({"6130": 40, "7900": 1}), "ค่าปะยางรถเบอร์": Counter({"6110": 5})}
    acc = {"6130", "6110", "7900"}
    assert a.from_history("เติมน้ำมัน 2ฒค 3332 (52)", past, acc) == ("6130", "เติมน้ำมัน")
    assert a.from_history("ปะยางรถเบอร์ 21", past, acc)[0] == "6110"
    assert a.from_history("ส่วยตำรวจ", past, acc) is None                     # nothing close: the model is asked


def test_no_text_no_suggestion_and_a_model_failure_keeps_the_account(monkeypatch):
    assert a.suggest("  ")["account"] is None
    monkeypatch.setattr(a, "history", lambda: {})
    monkeypatch.setattr(a, "from_model", lambda t: None)
    assert a.suggest("ส่วยตำรวจ") == {"account": None, "source": None, "like": None}
    monkeypatch.setattr(a, "from_model", lambda t: "7910")
    assert a.suggest("ส่วยตำรวจ") == {"account": "7910", "source": "ai", "like": None}
