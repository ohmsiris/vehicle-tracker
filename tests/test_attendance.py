import sys
import types
import uuid
from datetime import date, datetime

import pytest

from app import attendance as at
from app import employees as em

TODAY = date(2026, 10, 27)
E1 = "11111111-1111-1111-1111-111111111111"
E2 = "22222222-2222-2222-2222-222222222222"
E3 = "33333333-3333-3333-3333-333333333333"


def dt(day, hhmm):
    return datetime.fromisoformat(f"{day}T{hhmm}:00")


def emp(eid, code, position="ผลิต", pay_group="company"):
    return {"id": eid, "code": code, "prefix": "นาย", "full_name": f"คน {code}", "position": position, "branch": "สระบุรี", "status": "active", "pay_group": pay_group}


# ------------------------------------------------------------------ pay months
def test_pay_months_run_from_the_26th_to_the_25th_and_are_named_by_the_25th():
    assert at.pay_month_of(date(2026, 8, 26)) == "2026-09" and at.pay_month_of(date(2026, 9, 25)) == "2026-09"
    assert at.pay_month_of(date(2026, 12, 26)) == "2027-01" and at.pay_month_of(date(2026, 1, 5)) == "2026-01"
    assert at.month_range("2026-09") == (date(2026, 8, 26), date(2026, 9, 25))
    assert at.month_range("2027-01") == (date(2026, 12, 26), date(2027, 1, 25))
    assert at.parse_month("2026-09") == "2026-09"
    with pytest.raises(at.InvalidAttendance):
        at.parse_month("2026-13")


# ------------------------------------------------------------------ pairing the scans
def test_a_day_shift_is_one_in_and_one_out():
    s = at.pair_shifts([dt("2026-09-01", "07:02"), dt("2026-09-01", "19:05")])
    assert len(s) == 1 and s[0]["out"] and at.minutes_of(s[0]) == 12 * 60 + 3


def test_a_night_shift_pairs_across_midnight_and_belongs_to_the_day_it_started():
    s = at.pair_shifts([dt("2026-09-01", "19:01"), dt("2026-09-02", "07:04"), dt("2026-09-02", "19:00"), dt("2026-09-03", "07:00")])
    assert [(x["in"].day, x["out"].day) for x in s] == [(1, 2), (2, 3)]


def test_the_same_scan_pressed_twice_counts_once():
    s = at.pair_shifts([dt("2026-09-01", "07:00"), dt("2026-09-01", "07:03"), dt("2026-09-01", "19:00")])
    assert len(s) == 1 and s[0]["in"].minute == 0


def test_a_lone_scan_is_a_single_scan_and_scans_too_far_apart_do_not_pair():
    s = at.pair_shifts([dt("2026-09-01", "07:00")])
    assert s == [{"in": dt("2026-09-01", "07:00"), "out": None}]
    far = at.pair_shifts([dt("2026-09-01", "07:00"), dt("2026-09-02", "07:00")])      # 24 hours apart
    assert [x["out"] for x in far] == [None, None]


def test_one_stray_evening_scan_does_not_drag_the_following_shifts_out_of_step():
    punches = [dt("2026-09-01", "03:58"), dt("2026-09-01", "16:32"), dt("2026-09-01", "20:00"),       # 20:00 is a stray scan
               dt("2026-09-02", "04:06"), dt("2026-09-02", "17:58"), dt("2026-09-03", "03:55"), dt("2026-09-03", "16:40")]
    s = at.pair_shifts(punches)
    pairs = [(x["in"].strftime("%d %H:%M"), x["out"].strftime("%d %H:%M") if x["out"] else None) for x in s]
    assert pairs == [("01 03:58", "01 16:32"), ("01 20:00", None), ("02 04:06", "02 17:58"), ("03 03:55", "03 16:40")]


# ------------------------------------------------------------------ what is flagged
def test_a_fixed_shift_is_short_when_more_than_ten_minutes_are_missing():
    ok = [{"in": dt("2026-09-01", "07:00"), "out": dt("2026-09-01", "18:50")}]           # 11:50 = exactly 10 short: fine
    short = [{"in": dt("2026-09-01", "07:00"), "out": dt("2026-09-01", "18:49")}]
    assert at.review_flag(ok, True) == "ok" and at.review_flag(short, True) == "short"
    assert at.review_flag(short, False) == "ok"                                           # free hours: never "short"
    assert at.review_flag(short, True, 9 * 60) == "ok"                          # Kaeng Khoi's shift is 9 hours
    assert at.review_flag([{"in": dt("2026-09-01", "07:00"), "out": None}], False) == "single"


# ------------------------------------------------------------------ the month
def month(punches, reviews=(), emps=None):
    emps = emps or {E1: emp(E1, "SB-FR001")}
    return at.build_month("2026-09", emps, punches, list(reviews))


def day_punches(*days, start="07:00", end="19:00"):
    return [t for d in days for t in (dt(d, start), dt(d, end))]


def test_the_first_three_single_scans_get_a_suggestion_and_the_fourth_does_not():
    singles = [dt(f"2026-09-{d:02d}", "07:00") for d in (2, 4, 6, 8)]
    out = month({E1: singles + day_punches("2026-09-10")})
    days = out["people"][0]["days"]
    assert [d["flag"] for d in days] == ["single"] * 4 + ["ok"]
    assert [d["suggest"] for d in days] == ["half", "half", "half", None, None]
    assert [d["single_no"] for d in days] == [1, 2, 3, 4, None]
    assert out["totals"]["single"] == 4 and out["totals"]["open"] == 4 and out["totals"]["suggested"] == 3


def test_a_decision_closes_the_day_and_is_counted():
    rv = [{"employee_id": E1, "work_date": "2026-09-02", "decision": "full_penalty", "deduct_amount": 300, "note": "x", "manual": False}]
    out = month({E1: [dt("2026-09-02", "07:00"), dt("2026-09-04", "07:00")]}, rv)
    p = out["people"][0]
    assert p["counts"]["open"] == 1 and p["counts"]["decided"] == 1 and p["deduct"] == 300.0
    assert p["days"][0]["review"]["label"] == "ค่าแรงเต็ม + ใบเตือน + หักเงิน" and p["days"][0]["suggest"] == "half"


def test_only_the_days_of_the_pay_month_are_shown_and_a_night_shift_counts_on_its_start_day():
    punches = [dt("2026-08-25", "19:00"), dt("2026-08-26", "07:00"),               # started the day before the period: not in it
               dt("2026-09-25", "19:00"), dt("2026-09-26", "07:00"),               # starts on the 25th: in it, though it ends on the 26th
               dt("2026-09-26", "19:00"), dt("2026-09-27", "07:00")]               # starts on the 26th: next month
    days = month({E1: punches})["people"][0]["days"]
    assert [d["date"] for d in days] == ["2026-09-25"] and days[0]["shifts"][0]["next_day"] is True and days[0]["flag"] == "ok"


def test_a_short_fixed_shift_is_flagged_with_no_suggestion_and_free_roles_are_not():
    punches = [dt("2026-09-02", "07:00"), dt("2026-09-02", "16:00")]                  # 9 hours
    assert month({E1: punches})["people"][0]["days"][0]["flag"] == "short"
    assert month({E1: punches})["people"][0]["days"][0]["suggest"] is None
    kk = month({E1: punches}, emps={E1: {**emp(E1, "SB-KK001"), "branch": "แก่งคอย"}})                  # 9 hours is a full shift at Kaeng Khoi
    assert kk["people"][0]["days"][0]["flag"] == "ok" and kk["people"][0]["expected"] == 540
    free = month({E1: punches}, emps={E1: emp(E1, "SB-FR001", position="ช่าง")})
    assert free["people"][0]["days"][0]["flag"] == "ok" and free["totals"]["open"] == 0


def test_a_day_added_by_the_owner_without_a_scan_shows_up_and_hq_paid_people_do_not():
    rv = [{"employee_id": E1, "work_date": "2026-09-05", "decision": "approve", "deduct_amount": 0, "manual": True}]
    out = month({E1: day_punches("2026-09-02")}, rv)
    assert [(d["date"], d["flag"]) for d in out["people"][0]["days"]] == [("2026-09-02", "ok"), ("2026-09-05", "manual")]
    hq = month({E1: day_punches("2026-09-02")}, emps={E1: emp(E1, "SB-FR005", pay_group="hq")})
    assert hq["people"] == [] and hq["hq_skipped"] == 1


def test_two_shifts_on_one_day_are_one_day_with_two_shifts():
    out = month({E1: [dt("2026-09-02", "07:00"), dt("2026-09-02", "16:00"), dt("2026-09-02", "16:30"), dt("2026-09-03", "00:30")]})
    day = out["people"][0]["days"][0]
    assert len(day["shifts"]) == 2 and day["minutes"] == 9 * 60 + 8 * 60


# ------------------------------------------------------------------ a decision from the form
def test_a_decision_is_cleaned_and_bad_ones_are_refused_in_thai():
    v = at.clean_review({"employee_id": E1, "work_date": "2026-09-02", "decision": "full_penalty", "deduct_amount": "1,000", "note": " ขาดบ่อย "}, today=TODAY)
    assert v["deduct_amount"] == 1000.0 and v["note"] == "ขาดบ่อย" and v["manual"] is False
    assert at.clean_review({"employee_id": E1, "work_date": "2026-09-02", "decision": "half", "deduct_amount": 500}, today=TODAY)["deduct_amount"] == 0     # only a penalty deducts
    assert at.clean_review({"employee_id": E1, "work_date": "2026-09-02", "decision": ""}, today=TODAY)["decision"] == ""
    for data, frag in (({"employee_id": "x", "work_date": "2026-09-02"}, "ไม่พบพนักงาน"), ({"employee_id": E1, "work_date": "2569-09-02", "decision": "half"}, "วันที่"),
                       ({"employee_id": E1, "work_date": "2026-09-02", "decision": "zz"}, "เลือกการตัดสิน"),
                       ({"employee_id": E1, "work_date": "2026-09-02", "decision": "full_penalty", "deduct_amount": "-5"}, "ไม่ติดลบ"),
                       ({"employee_id": E1, "work_date": "2026-09-02", "decision": "full_penalty", "deduct_amount": "abc"}, "ไม่ถูกต้อง")):
        with pytest.raises(at.InvalidAttendance, match=frag):
            at.clean_review(data, today=TODAY)


# ------------------------------------------------------------------ reading the machine's file
class FakeSheet:
    def __init__(self, rows):
        self.rows = rows
        self.nrows = len(rows)
        self.ncols = max(len(r) for r in rows)

    def cell_value(self, r, c):
        row = self.rows[r]
        return row[c] if c < len(row) else ""


def fake_xlrd(monkeypatch, rows, names=(at.SHEET,)):
    sheet = FakeSheet(rows)
    book = types.SimpleNamespace(sheet_names=lambda: list(names), sheet_by_name=lambda n: sheet)
    monkeypatch.setitem(sys.modules, "xlrd", types.SimpleNamespace(open_workbook=lambda file_contents=None: book))


REPORT = [["รายงาน"] + [""] * 4, [""] * 5, ["ช่วงเวลา:", "", "2026-08-26 ~ 2026-08-30", "", ""], ["26.0", "27.0", "28.0", "29.0", "30.0"],
          ["ID:", "", "sb-fr001", "", "", "", "", "", "ชื่อ:", "", "สมชาย ใจดี"], ["07:0219:05", "", "19:00", "", "07:00"],
          ["ID:", "", "SB-ZZ999", "", "", "", "", "", "ชื่อ:", "", "คนไม่มี"], ["07:00", "", "", "", ""]]


def test_the_report_is_read_into_scans_per_person_and_day(monkeypatch):
    fake_xlrd(monkeypatch, REPORT)
    r = at.parse_report(b"x")
    assert (r["start"], r["end"]) == (date(2026, 8, 26), date(2026, 8, 30))
    assert [p["code"] for p in r["people"]] == ["SB-FR001", "SB-ZZ999"]
    assert r["people"][0]["name"] == "สมชาย ใจดี"
    assert r["people"][0]["punches"] == [dt("2026-08-26", "07:02"), dt("2026-08-26", "19:05"), dt("2026-08-28", "19:00"), dt("2026-08-30", "07:00")]


def test_a_file_that_is_not_the_machine_report_is_refused_in_thai(monkeypatch):
    fake_xlrd(monkeypatch, REPORT, names=("Sheet1",))
    with pytest.raises(at.InvalidAttendance, match="ไม่พบแผ่นงาน"):
        at.parse_report(b"x")
    fake_xlrd(monkeypatch, [["a"], ["b"]])
    with pytest.raises(at.InvalidAttendance, match="ช่วงวันที่"):
        at.parse_report(b"x")


# ------------------------------------------------------------------ the database
class Fake:
    def __init__(self):
        self.t = {"employees": [emp(E1, "SB-FR001")], "attendance_punches": [], "attendance_reviews": [], "attendance_uploads": []}

    def _eq(self, rows, params):
        eq = {k: v[3:] for k, v in (params or {}).items() if isinstance(v, str) and v.startswith("eq.")}
        return [r for r in rows if all(str(r.get(k)) == v for k, v in eq.items())]

    def select(self, table, params=None):
        return [dict(r) for r in self._eq(self.t[table], params)]

    select_all = select

    def insert(self, table, rows, on_conflict=None, ignore_duplicates=False):
        out = []
        for r in rows:
            keys = on_conflict.split(",") if on_conflict else []
            hit = next((x for x in self.t[table] if keys and all(x.get(k) == r.get(k) for k in keys)), None)
            if hit:
                if not ignore_duplicates:
                    hit.update(r)
                    out.append(dict(hit))
                continue
            row = {"id": str(uuid.uuid4()), **r}
            self.t[table].append(row)
            out.append(dict(row))
        return out

    def delete(self, table, match):
        for r in self._eq(self.t[table], match):
            self.t[table].remove(r)


@pytest.fixture
def db(monkeypatch):
    d = Fake()
    for name in ("select", "select_all", "insert", "delete"):
        monkeypatch.setattr(at.supabase, name, getattr(d, name))
    monkeypatch.setattr(at, "has_v26", lambda: True)
    monkeypatch.setattr(em, "has_v21", lambda: True)
    monkeypatch.setattr(em, "today_th", lambda: TODAY)
    monkeypatch.setattr(em, "_get", lambda i: next(r for r in d.t["employees"] if r["id"] == i))
    return d


def test_uploading_a_report_adds_its_scans_once_and_names_the_people_it_skipped(db, monkeypatch):
    fake_xlrd(monkeypatch, REPORT)
    r = at.import_report(b"x", "1_report (8).xls")
    assert r["people"] == 1 and r["punches_read"] == 4 and r["punches_added"] == 4 and r["unknown"] == ["SB-ZZ999 คนไม่มี"] and r["month"] == "2026-09"
    again = at.import_report(b"x", "1_report (8).xls")
    assert again["punches_added"] == 0 and len(db.t["attendance_punches"]) == 4 and len(db.t["attendance_uploads"]) == 2


def test_a_decision_is_saved_replaced_and_taken_back(db):
    base = {"employee_id": E1, "work_date": "2026-09-02"}
    assert at.set_review({**base, "decision": "half"})["review"]["decision"] == "half"
    assert at.set_review({**base, "decision": "full_penalty", "deduct_amount": 300})["review"]["deduct_amount"] == 300.0
    assert len(db.t["attendance_reviews"]) == 1 and db.t["attendance_reviews"][0]["decision"] == "full_penalty"
    assert at.set_review({**base, "decision": ""}) == {"review": None} and db.t["attendance_reviews"] == []


def test_applying_the_suggestions_decides_only_the_open_ones(db, monkeypatch):
    punches = {E1: [dt(f"2026-09-{d:02d}", "07:00") for d in (2, 4, 6, 8)]}
    reviews = [{"employee_id": E1, "work_date": "2026-09-02", "decision": "unpaid", "deduct_amount": 0, "manual": False}]
    monkeypatch.setattr(at, "_load_month", lambda m: at.build_month(m, {E1: emp(E1, "SB-FR001")}, punches, reviews))
    assert at.apply_suggestions("2026-09") == {"applied": 2}                      # single scans no. 2 and 3 are open and suggested; no. 1 already has a decision, no. 4 has no suggestion
    rows = {r["work_date"]: r["decision"] for r in db.t["attendance_reviews"]}
    assert rows == {"2026-09-04": "half", "2026-09-06": "half"}                  # the earlier "unpaid" lives in the real table, not this fake


def test_the_schema_is_missing_message_and_the_employee_lock(db, monkeypatch):
    monkeypatch.setattr(at, "has_v26", lambda: False)
    assert at.get_month("2026-09") == {"ready": False}
    with pytest.raises(at.InvalidAttendance, match="schema_v26"):
        at.set_review({"employee_id": E1, "work_date": "2026-09-02", "decision": "half"})
    assert ("attendance_punches", "employee_id") in em.REFERENCED_BY and ("attendance_reviews", "employee_id") in em.REFERENCED_BY


# ------------------------------------------------------------------ the worker's own shift time, and overtime
def kk(eid=E1, code="SB-KK001", position="ผลิต", **extra):
    return {**emp(eid, code, position=position), "branch": "แก่งคอย", **extra}


def test_a_clock_time_and_a_shift_length_are_read_past_midnight_too():
    assert at.parse_clock("08:00") == 480 and at.parse_clock("08:00:00") == 480 and at.parse_clock("") is None and at.parse_clock(None) is None
    assert at.clock_text(at.parse_clock("8:05")) == "08:05"
    assert at.schedule_minutes("08:00", "17:00") == 540 and at.schedule_minutes("20:00", "08:00") == 720
    assert at.schedule_minutes("08:00", "") is None and at.schedule_minutes("08:00", "08:00") is None
    for bad in ("25:00", "8", "ab", "12:61"):
        with pytest.raises(at.InvalidAttendance, match="เวลาไม่ถูกต้อง"):
            at.parse_clock(bad)


def test_a_workers_own_shift_time_replaces_the_default_for_him_only():
    nine = [dt("2026-09-02", "08:00"), dt("2026-09-02", "17:00")]
    sar = {E1: emp(E1, "SB-FR001")}                                                    # Saraburi ผลิต: 12 h by default, so 9 h is short
    assert month({E1: nine}, emps=sar)["people"][0]["days"][0]["flag"] == "short"
    own = {E1: {**emp(E1, "SB-FR001"), "shift_start": "08:00:00", "shift_end": "17:00:00"}}
    p = month({E1: nine}, emps=own)["people"][0]
    assert p["days"][0]["flag"] == "ok" and p["expected"] == 540 and p["schedule"] == {"start": "08:00", "end": "17:00"}
    free = {E1: {**emp(E1, "SB-FR001", position="ช่าง"), "shift_start": "08:00", "shift_end": "17:00"}}      # a free-hours role with a set time is checked against it
    assert month({E1: [dt("2026-09-02", "08:00"), dt("2026-09-02", "14:00")]}, emps=free)["people"][0]["days"][0]["flag"] == "short"


def test_kaeng_khoi_ผลิต_work_past_the_nine_hour_shift_is_overtime():
    punches = [dt("2026-09-02", "08:00"), dt("2026-09-02", "17:05"),            # 9:05: within the 10 minute grace, no OT
               dt("2026-09-03", "08:00"), dt("2026-09-03", "20:10"),            # 12:10: 3:10 of OT
               dt("2026-09-04", "08:00"), dt("2026-09-04", "16:55")]            # 8:55: not short, no OT
    p = month({E1: punches}, emps={E1: kk()})["people"][0]
    assert [d["flag"] for d in p["days"]] == ["ok", "ok", "ok"] and [d["ot_minutes"] for d in p["days"]] == [0, 190, 0]
    assert p["counts"]["ot_minutes"] == 190 and p["ot_applies"] is True
    out = month({E1: punches}, emps={E1: kk()})
    assert out["totals"]["ot_minutes"] == 190
    other = month({E1: punches}, emps={E1: emp(E1, "SB-FR001")})["people"][0]      # Saraburi has no OT
    assert other["ot_applies"] is False and other["counts"]["ot_minutes"] == 0
    wash = month({E1: punches}, emps={E1: kk(position="ล้างกระสอบ")})["people"][0]  # only ผลิต at Kaeng Khoi
    assert wash["counts"]["ot_minutes"] == 0


def test_the_settings_list_and_save_a_workers_shift_time(db, monkeypatch):
    def update(table, match, values):
        rows = db._eq(db.t[table], match)
        for r in rows:
            r.update(values)
        return rows
    monkeypatch.setattr(at.supabase, "update", update)
    monkeypatch.setattr(at, "has_v27", lambda: True)
    db.t["employees"].append({**kk(E2, "SB-KK002"), "pay_group": "hq"})
    first = at.list_schedules()
    assert [p["code"] for p in first["people"]] == ["SB-FR001"] and first["people"][0]["default_minutes"] == 720 and first["people"][0]["start"] is None     # HQ-paid are left out
    out = at.set_schedule({"employee_id": E1, "start": "08:00", "end": "17:00"})
    assert out == {"employee_id": E1, "start": "08:00", "end": "17:00", "minutes": 540}
    assert at.list_schedules()["people"][0]["minutes"] == 540
    assert at.set_schedule({"employee_id": E1, "start": "", "end": ""})["minutes"] is None and db.t["employees"][0]["shift_start"] is None
    for data, frag in (({"employee_id": E1, "start": "08:00", "end": ""}, "ให้ครบทั้งสองช่อง"), ({"employee_id": E1, "start": "08:00", "end": "08:00"}, "ไม่เป็นเวลาเดียวกัน"),
                       ({"employee_id": "x", "start": "", "end": ""}, "ไม่พบพนักงาน"), ({"employee_id": E1, "start": "99:00", "end": "17:00"}, "เวลาไม่ถูกต้อง")):
        with pytest.raises(at.InvalidAttendance, match=frag):
            at.set_schedule(data)
    monkeypatch.setattr(at, "has_v27", lambda: False)
    with pytest.raises(at.InvalidAttendance, match="schema_v27"):
        at.set_schedule({"employee_id": E1, "start": "08:00", "end": "17:00"})
