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
    assert out == [{"day": "2026-11-11", "qty": {"zong": 12, "lod": 1250}, "money": 18450.5, "spare": False, "spare_by": None},
                   {"day": "2026-11-13", "qty": {}, "money": 0, "spare": False, "spare_by": None}]
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


def test_a_spare_day_counts_for_nobody():
    """Owner, 17 Nov 2026: the spare only drives and delivers (no commission) -- the truck's driver has the day off, the sales count for nobody."""
    people = [{"id": "A", "name": "เอ", "nickname": "", "truck": "5"}, {"id": "B", "name": "บี", "nickname": "", "truck": "3"}]
    days = ["2026-11-11", "2026-11-12", "2026-11-13"]
    rows = [{"employee_id": "A", "day": "2026-11-11", "qty": {"zong": 10}, "money": 1000, "spare": False},
            {"employee_id": "A", "day": "2026-11-12", "qty": {"zong": 7}, "money": 700, "spare": True}]
    out = dd.summarize(rows, people, days)
    assert [x["id"] for x in out] == ["B", "A"]                                   # by truck number
    a = out[1]
    assert (a["days_worked"], a["days_off"], a["spare_days"]) == (1, 2, 1) and a["qty"]["zong"] == 10 and a["money"] == 1000
    assert a["off_dates"] == ["2026-11-12", "2026-11-13"] and a["spare_dates"] == ["2026-11-12"]
    assert out[0]["days_off"] == 3 and out[0]["money"] == 0


def test_rows_keep_the_spare_tick():
    rows = [{"day": "2026-11-11", "qty": {"zong": "5"}, "money": "500", "spare": True},
            {"day": "2026-11-12", "qty": {}, "money": "", "spare": True},                 # ticked, figures not typed: still kept
            {"day": "2026-11-13", "qty": {"zong": "1"}, "money": ""},
            {"day": "2026-11-14", "qty": {}, "money": "", "spare": False}]                # nothing at all: no row
    out = dd.clean_rows(rows, "2026-11", 2)
    assert [(r["day"], r["spare"]) for r in out] == [("2026-11-11", True), ("2026-11-12", True), ("2026-11-13", False)]


def test_export_has_a_summary_tab_then_a_tab_per_driver(monkeypatch):
    import io
    import openpyxl
    people = [{"id": "A", "name": "สมชาย ใจดี", "nickname": "ชาย", "truck": "5"}, {"id": "B", "name": "สมศักดิ์ ดี", "nickname": "ศักดิ์", "truck": "3"}]
    rows = [{"employee_id": "A", "day": "2026-11-11", "qty": {"zong": 10, "lod": 2}, "money": 1000.0, "spare": False},
            {"employee_id": "A", "day": "2026-11-12", "qty": {"zong": 7}, "money": 700.0, "spare": True}]
    monkeypatch.setattr(dd, "spares", lambda: [{**p, "kind": "สาย"} for p in people])
    monkeypatch.setattr(dd, "_period_rows", lambda a, b: rows)
    data, name = dd.export("2026-11", 2)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["สรุป", "#3 ศักดิ์", "#5 ชาย"] and name.endswith("งวด 2.xlsx")
    s = wb["สรุป"]
    assert s["A6"].value.startswith("สมชาย") and (s["C6"].value, s["D6"].value, s["E6"].value, s["F6"].value) == (1, 14, 1, 0)
    assert s["G6"].value == 10 and s["M6"].value == 1000
    a = wb["#5 ชาย"]
    assert a["A5"].value.endswith("11/11/2569") and a["B5"].value == 10 and a["I6"].value == "สำรองขับ — ไม่นับ" and a["I7"].value == "หยุด"
    total = [r for r in a.iter_rows(min_row=5) if r[0].value and str(r[0].value).startswith("รวม")][0]
    assert total[1].value == 10 and total[7].value == 1000                         # the spare's 7 and 700 are not counted


def test_who_the_spare_was_is_kept_and_counted_without_sales():
    people = [{"id": "A", "name": "เอ", "nickname": "", "truck": "5", "kind": "สาย"},
              {"id": "F", "name": "หน้าโรงคนหนึ่ง", "nickname": "", "truck": None, "kind": "หน้าโรง"},
              {"id": "G", "name": "หน้าโรงอีกคน", "nickname": "", "truck": None, "kind": "หน้าโรง"}]
    days = ["2026-11-11", "2026-11-12"]
    rows = [{"employee_id": "A", "day": "2026-11-12", "qty": {"zong": 7}, "money": 700, "spare": True, "spare_by": "F"}]
    out = {x["id"]: x for x in dd.summarize(rows, people, days)}
    assert "G" not in out                                                            # a หน้าโรง driver shows only when he drove as the spare
    assert (out["F"]["as_spare_days"], out["F"]["days_off"], out["F"]["money"]) == (1, 1, 0)
    assert (out["A"]["spare_days"], out["A"]["days_off"], out["A"]["money"]) == (1, 2, 0)
    kept = dd.clean_rows([{"day": "2026-11-12", "qty": {}, "spare": True, "spare_by": "F"},
                          {"day": "2026-11-13", "qty": {"zong": 1}, "spare_by": "F"}], "2026-11", 2, owner="A", spares={"F"})
    assert [(r["spare"], r["spare_by"]) for r in kept] == [(True, "F"), (False, None)]        # a name only counts on a สำรองขับ day
    with pytest.raises(dd.DriverDaysError, match="สำรอง"):
        dd.clean_rows([{"day": "2026-11-12", "qty": {}, "spare": True, "spare_by": "A"}], "2026-11", 2, owner="A", spares={"A", "F"})
