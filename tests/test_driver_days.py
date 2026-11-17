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
    assert out == [{"day": "2026-11-11", "qty": {"zong": 12, "lod": 1250}, "money": 18450.5, "driven_by": None},
                   {"day": "2026-11-13", "qty": {}, "money": 0, "driven_by": None}]
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


def test_a_spare_drivers_day_counts_for_the_spare_not_the_truck_driver():
    people = [{"id": "A", "name": "เอ", "nickname": "", "truck": "5"}, {"id": "S", "name": "สำรอง", "nickname": "", "truck": None}]
    days = ["2026-11-11", "2026-11-12", "2026-11-13"]
    rows = [{"employee_id": "A", "day": "2026-11-11", "qty": {"zong": 10}, "money": 1000, "driven_by": None},
            {"employee_id": "A", "day": "2026-11-12", "qty": {"zong": 7}, "money": 700, "driven_by": "S"}]
    out = {x["id"]: x for x in dd.summarize(rows, people, days)}
    a, s = out["A"], out["S"]
    assert (a["own_days"], a["spare_days"], a["days_worked"], a["days_off"], a["covered_days"]) == (1, 0, 1, 2, 1)
    assert a["qty"]["zong"] == 10 and a["money"] == 1000 and a["off_dates"] == ["2026-11-12", "2026-11-13"]
    assert (s["spare_days"], s["days_worked"], s["days_off"]) == (1, 1, 2) and s["qty"]["zong"] == 7 and s["money"] == 700


def test_rows_keep_who_drove():
    rows = [{"day": "2026-11-11", "qty": {"zong": "5"}, "money": "500", "driven_by": "S"},
            {"day": "2026-11-12", "qty": {}, "money": "", "driven_by": "S"},              # the spare drove, figures to come
            {"day": "2026-11-13", "qty": {"zong": "1"}, "money": "", "driven_by": "A"}]   # the sheet's own driver: not a spare
    out = dd.clean_rows(rows, "2026-11", 2, owner="A", spares={"S", "A"})
    assert [(r["day"], r["driven_by"]) for r in out] == [("2026-11-11", "S"), ("2026-11-12", "S"), ("2026-11-13", None)]
    with pytest.raises(dd.DriverDaysError, match="คนขับแทน"):
        dd.clean_rows([{"day": "2026-11-11", "qty": {"zong": 1}, "driven_by": "X"}], "2026-11", 2, owner="A", spares={"S"})


def test_export_has_a_summary_tab_then_a_tab_per_driver(monkeypatch):
    import io
    import openpyxl
    people = [{"id": "A", "name": "สมชาย ใจดี", "nickname": "ชาย", "truck": "5", "kind": "สาย"},
              {"id": "S", "name": "สมหญิง สำรอง", "nickname": "หญิง", "truck": None, "kind": "สาย"}]
    rows = [{"employee_id": "A", "day": "2026-11-11", "qty": {"zong": 10, "lod": 2}, "money": 1000.0, "driven_by": None},
            {"employee_id": "A", "day": "2026-11-12", "qty": {"zong": 7}, "money": 700.0, "driven_by": "S"}]
    monkeypatch.setattr(dd, "spares", lambda: people)
    monkeypatch.setattr(dd, "_period_rows", lambda a, b: rows)
    data, name = dd.export("2026-11", 2)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames[0] == "สรุป" and wb.sheetnames[1:] == ["#5 ชาย", "หญิง"] and name.endswith("งวด 2.xlsx")
    s = wb["สรุป"]
    assert s["A5"].value.startswith("สมชาย") and s["C5"].value == 1 and s["E5"].value == 14 and s["L5"].value == 1000
    a = wb["#5 ชาย"]
    assert a["A5"].value.endswith("11/11/2569") and a["B5"].value == 10 and a["I6"].value == "หยุด — หญิง ขับแทน" and len([r for r in a.iter_rows(min_row=5) if r[0].value]) == 16
    h = wb["หญิง"]
    assert h["B6"].value == 7 and h["I6"].value.startswith("ขับแทน #5")
