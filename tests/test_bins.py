"""Bins as records (app/bins.py)."""
import pytest

from app import bins as bn


def test_parse_keeps_the_letter_and_the_unnumbered():
    found, rest = bn.parse("V200-1805, S200-0412, 500L, T800")
    assert [(b["model"], b["size"], b["num"], b["number"]) for b in found] == [("V", 200, 1805, "1805"), ("S", 200, 412, "0412")]
    assert rest == "500L, T800"
    assert bn.code(found[1]) == "S200-0412"


def test_clean_and_text():
    assert bn.clean({"model": "v", "size": "200", "number": "0412"}) == {"model": "V", "size": 200, "num": 412, "number": "0412"}
    with pytest.raises(bn.BinError):
        bn.clean({"model": "V", "size": "", "number": "1"})
    assert bn.text_for([{"model": "V", "size": 450, "num": 3, "number": "3"}, {"model": "S", "size": 200, "num": 9, "number": "9"}], "500L") \
        == "S200-9, V450-3, 500L"


def test_a_bin_on_two_sheets_is_a_clash_on_the_second():
    custs = [{"id": 1, "bins_legacy": "V200-1787"}, {"id": 2, "bins_legacy": "V200-1787, C200-5"}]
    have = {1: [{"model": "V", "size": 200, "num": 1787}], 2: [{"model": "C", "size": 200, "num": 5}]}
    assert bn.clashes_for(custs, have) == {2: ["V200-1787"]}
