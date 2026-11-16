from datetime import date

import pytest

from app import driver_days as dd, users


def test_half_months_run_26_to_10_and_11_to_25():
    assert dd.period_of(date(2026, 10, 26)) == ("2026-11", 1)
    assert dd.period_of(date(2026, 11, 10)) == ("2026-11", 1)
    assert dd.period_of(date(2026, 11, 11)) == ("2026-11", 2)
    assert dd.period_of(date(2026, 11, 25)) == ("2026-11", 2)
    assert dd.period_of(date(2026, 12, 28)) == ("2027-01", 1)
    first = dd.days_of("2026-11", 1)
    assert first[0] == date(2026, 10, 26) and first[-1] == date(2026, 11, 10) and len(first) == 16
    second = dd.days_of("2026-11", 2)
    assert second[0] == date(2026, 11, 11) and second[-1] == date(2026, 11, 25) and len(second) == 15


def test_rows_keep_only_filled_days_and_numbers():
    rows = [{"day": "2026-11-11", "qty": {"zong": "12", "lod": "1,250", "mo": ""}, "money": "18,450.50"},
            {"day": "2026-11-12", "qty": {"zong": ""}, "money": ""},                        # nothing typed: no row
            {"day": "2026-11-13", "qty": {}, "money": "0"}]                                  # 0 baht is a figure
    out = dd.clean_rows(rows, "2026-11", 2)
    assert out == [{"day": "2026-11-11", "qty": {"zong": 12, "lod": 1250}, "money": 18450.5},
                   {"day": "2026-11-13", "qty": {}, "money": 0}]
    assert dd.totals(out) == {"qty": {"zong": 12, "kak": 0, "lod": 1250, "lek": 0, "pack": 0, "mo": 0}, "money": 18450.5, "days": 2}


@pytest.mark.parametrize("row, words", [
    ({"day": "2026-11-05", "qty": {"zong": "1"}}, "ไม่อยู่ในงวด"),            # a day of the other half
    ({"day": "2026-11-11", "qty": {"zong": "abc"}}, "ไม่ใช่ตัวเลข"),
    ({"day": "2026-11-11", "qty": {"zong": "-3"}}, "ไม่ถูกต้อง"),
    ({"day": "2026-11-11", "qty": {"zong": "2.5"}}, "จำนวนเต็ม"),
])
def test_bad_rows_are_refused(row, words):
    with pytest.raises(dd.DriverDaysError, match=words):
        dd.clean_rows([row], "2026-11", 2)


def test_the_page_is_a_menu_of_its_own():
    assert users.MENUS["driver_days"]["group"] == "รถสาย"
    assert users.allowed(["driver_days"], "/api/driver-days/abc") and not users.allowed(["sales_sheets"], "/api/driver-days")
