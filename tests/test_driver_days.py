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
    assert out == [{"day": "2026-11-11", "qty": {"zong": 12, "lod": 1250}, "money": 18450.5, "spare": False, "spare_by": None, "money_parts": None},
                   {"day": "2026-11-13", "qty": {}, "money": 0, "spare": False, "spare_by": None, "money_parts": None}]
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
    monkeypatch.setattr(dd, "split_ids", lambda: set())
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


def test_split_drivers_type_two_sales_and_the_money_is_their_sum(monkeypatch):
    """Owner, 17 Nov 2026: ทวาย, สำเริง, สะอาด -- ยอดขายซอง and ยอดขายหลอด in baht; เงินที่ได้ = their sum, worked out, never typed."""
    rows = [{"day": "2026-11-11", "qty": {"zong": "10"}, "money": "999", "parts": {"zong": "1,250.50", "lod": "300"}},
            {"day": "2026-11-12", "qty": {}, "money": "500", "parts": {}}]                # parts empty: no money (the typed 500 is ignored)
    out = dd.clean_rows(rows, "2026-11", 2, split=True)
    assert out[0]["money"] == 1550.5 and out[0]["money_parts"] == {"zong": 1250.5, "lod": 300}
    assert len(out) == 1                                                           # day 2 had nothing but the ignored money
    plain = dd.clean_rows(rows, "2026-11", 2)
    assert plain[0]["money"] == 999 and plain[0]["money_parts"] is None
    import io
    import openpyxl
    people = [{"id": "T", "name": "ทวาย กำจันทร์", "nickname": "เฉิ่ม", "truck": "7"}]
    saved = [{"employee_id": "T", "day": "2026-11-11", "qty": {"zong": 10}, "money": 1550.5, "spare": False, "money_parts": {"zong": 1250.5, "lod": 300}}]
    monkeypatch.setattr(dd, "spares", lambda: [{**p, "kind": "สาย"} for p in people])
    monkeypatch.setattr(dd, "_period_rows", lambda a, b: saved)
    monkeypatch.setattr(dd, "split_ids", lambda: {"T"})
    ws = openpyxl.load_workbook(io.BytesIO(dd.export("2026-11", 2)[0]))["#7 เฉิ่ม"]
    assert ws["H4"].value == "ยอดขายซอง (บาท)" and ws["I4"].value == "ยอดขายหลอด (บาท)" and ws["J4"].value == "เงินที่ได้ (บาท)"
    assert (ws["H5"].value, ws["I5"].value, ws["J5"].value) == (1250.5, 300, 1550.5) and ws["H5"].number_format == "#,##0.00"


def test_phone_sheet_figures_are_offered_day_by_day():
    from app import driver_days as dd
    days = ["2026-11-11", "2026-11-12", "2026-11-13"]
    got = dd.phone_suggestions([
        {"day": "2026-11-11", "status": "closed", "totals": {"sold": {"zong": 3, "kak": 0, "lod": 73.0, "mo": 11}, "amount": 4877.0, "commission_sales": 4877.0}},
        {"day": "2026-11-12", "status": "open", "totals": {"sold": {"zong": 0, "lod": 0}, "amount": 0}},          # nothing sold: no suggestion
        {"day": "2026-11-13", "status": "open", "totals": {"sold": {"lod": 40}, "amount": 3970.0}},                 # old totals: amount
        {"day": "2026-10-20", "status": "closed", "totals": {"sold": {"lod": 9}, "amount": 1}},                     # outside the half-month
    ], days)
    assert got == {"2026-11-11": {"qty": {"zong": 3, "lod": 73, "mo": 11}, "money": 4877.0, "sent": True},
                   "2026-11-13": {"qty": {"lod": 40}, "money": 3970.0, "sent": False}}


# ---- one whole month per page (owner, 26 Nov 2026), and an old half-month page can never wipe the other half
def test_half_zero_is_the_whole_company_month():
    assert dd._check_period("2026-11", 0) == ("2026-11", 0) and dd._check_period("2026-11", None) == ("2026-11", 0)
    days = dd.days_of("2026-11", 0)
    assert (days[0], days[-1], len(days)) == (date(2026, 10, 26), date(2026, 11, 25), 31)
    rows = dd.clean_rows([{"day": "2026-10-27", "money": 100}, {"day": "2026-11-20", "money": 200}], "2026-11", 0)
    assert [r["day"] for r in rows] == ["2026-10-27", "2026-11-20"]
    with pytest.raises(dd.DriverDaysError):
        dd._check_period("2026-11", 3)


def test_saving_replaces_only_the_period_the_page_showed(monkeypatch):
    deleted, inserted = [], []
    monkeypatch.setattr(dd, "_employee", lambda i: {"id": i, "full_name": "x", "nickname": ""})
    monkeypatch.setattr(dd, "spares", lambda: [])
    monkeypatch.setattr(dd, "split_ids", lambda: set())
    monkeypatch.setattr(dd.supabase, "delete", lambda t, m: deleted.append(m))
    monkeypatch.setattr(dd.supabase, "insert", lambda t, rows: inserted.extend(rows))
    monkeypatch.setattr(dd, "sheet", lambda e, m, h: {"half": h})
    E = "11111111-1111-1111-1111-111111111111"
    dd.save(E, "2026-11", 2, [{"day": "2026-11-20", "money": 5}])          # an old half-month page: only 11-25 is replaced
    assert deleted[-1]["day"] == "gte.2026-11-11" and deleted[-1]["and"] == "(day.lte.2026-11-25)"
    dd.save(E, "2026-11", 0, [{"day": "2026-10-27", "money": 5}])          # the new page: the whole month
    assert deleted[-1]["day"] == "gte.2026-10-26" and deleted[-1]["and"] == "(day.lte.2026-11-25)"
    with pytest.raises(dd.DriverDaysError):                                # a half-month page cannot save a day outside its half
        dd.save(E, "2026-11", 2, [{"day": "2026-10-27", "money": 5}])


def test_route_drivers_of_both_branches_with_their_own_trucks(monkeypatch):
    tables = {
        "drivers": [{"id": "d1", "name": "สมชาย ใจดี"}, {"id": "d2", "name": "หนึ่ง ชื่นชม"}],
        "vehicles": [{"vehicle_number": "6", "main_driver_id": "d1", "usage": "รถสาย", "branch": "สระบุรี"},
                     {"vehicle_number": "6", "main_driver_id": "d2", "usage": "รถสาย", "branch": "แก่งคอย"}],
        "employees": [{"id": "e2", "code": "SB-KK004", "full_name": "หนึ่ง ชื่นชม", "nickname": "หนึ่ง", "branch": "แก่งคอย"},
                      {"id": "e1", "code": "SB-PD001", "full_name": "สมชาย ใจดี", "nickname": "ชาย", "branch": "สระบุรี"}],
    }
    monkeypatch.setattr(dd.supabase, "select_all", lambda t, p: tables[t])
    got = dd.drivers()
    assert [(d["id"], d["truck"], d["branch"]) for d in got] == [("e1", "6", "สระบุรี"), ("e2", "6", "แก่งคอย")]     # Saraburi first


def test_paper_fuel_is_not_booked_where_the_bookkeeper_has_it_but_her_missing_lines_stay():
    """Only the paper's fuel lines that ARE on the cash-fuel page are skipped (same day + amount, then amount for a misread date);
    a fuel line she left out (no truck) stays booked from the paper. The page total still includes everything (26 Nov 2026)."""
    from app import acc_docs, inbox_ai
    batch = {"batch": "docs:2026-11:สระบุรี:mid", "month": "2026-11", "branch": "สระบุรี", "covers": [], "files": {},
             "pages": [{"page": 1, "file_page": 1, "feed": "midmonth", "kind": "expense", "title": "รายการเบิก", "written_total": 4500,
                        "lines": [("27/10", "เติมน้ำมัน 86-0974", 2000.0, "6130", "น้ำมันเงินสด", {}),
                                  ("3/10", "เติมน้ำมัน 84-7169 (อ่านไม่ชัด)", 1000.0, "6130", "น้ำมันเงินสด", {}),     # really 31/10
                                  ("1/11", "เบิกค่าน้ำมันรถ", 500.0, "6130", "น้ำมันเงินสด", {}),                       # not on her page
                                  ("27/10", "โอที", 1000.0, "5410", "ค่าแรง", {})]}]}
    done = [{"day": "2026-10-27", "amount": 2000}, {"day": "2026-10-31", "amount": 1000}]
    skipped = inbox_ai.skip_cash_fuel(batch, done)
    assert sorted(x[2] for x in skipped) == [1000.0, 2000.0]
    out = acc_docs.build(batch, [])
    assert sorted((e["lines"][0]["account"], e["lines"][0]["debit"]) for e in out["entries"]) == [("5410", 1000.0), ("6130", 500.0)]
    assert out["docs"][0]["status"] == "ok"                                          # the page total still includes the skipped fuel


def test_cash_fuel_lines_become_6130_entries_under_the_halfs_papers():
    from app import accounting as acc
    v = {"id": "v1", "vehicle_number": "19", "plate_number": "1ฒถ 2085", "branch": "สระบุรี"}
    e = acc.cash_fuel_entry({"id": 7, "branch": "สระบุรี", "month": "2026-11", "batch": 2, "day": "2026-11-12", "amount": 1000,
                             "vehicle_id": "v1", "driver_name": "สมชาย", "checked": True, "dup_of": None}, v)
    assert e["source"] == "endmonth" and e["source_id"] == "cashfuel:2026-11:สระบุรี:7"
    assert [(l["account"], l["debit"], l["credit"], l["vehicle_id"]) for l in e["lines"]] == [("6130", 1000.0, 0.0, "v1"), ("1100", 0.0, 1000.0, None)]
    assert acc.cash_fuel_entry({"id": 8, "day": "2026-11-12", "amount": 500, "checked": False}, v) is None       # not checked yet
    assert acc.cash_fuel_entry({"id": 9, "day": "2026-11-12", "amount": 500, "checked": True, "dup_of": 7}, v) is None   # a duplicate


def test_papers_that_already_booked_fuel_keep_it_cash_fuel_is_not_added_twice():
    from app import accounting as acc
    covered = acc.cash_fuel_covered_by_papers([{"branch": "สระบุรี", "entry_date": "2026-11-15", "source_id": "docs:2026-11:สระบุรี:end:p1:l1"},
                                               {"branch": "สระบุรี", "entry_date": "2026-10-28", "source_id": "cashfuel:2026-11:สระบุรี:7"}])
    assert covered == {("สระบุรี", "2026-11", 2)}                     # the end-of-month papers' fuel; the cash-fuel page's own line is not "papers"
    late = acc.cash_fuel_entry({"id": 9, "branch": "สระบุรี", "month": "2026-11", "batch": 2, "day": "2026-11-12", "amount": 800, "checked": True}, None)
    early = acc.cash_fuel_entry({"id": 10, "branch": "สระบุรี", "month": "2026-11", "batch": 1, "day": "2026-10-28", "amount": 800, "checked": True}, None)
    assert acc.half_key(late) in covered and acc.half_key(early) not in covered
