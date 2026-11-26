"""คำนวณเงินเดือน, step one: the checklist of inputs (app/payroll.py) -- the pure builders with literal rows, the menu and the gate."""
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import payroll as pw
from app import users

TODAY = date(2026, 11, 26)
MONTH = "2026-11"                                   # 26/10/2026 - 25/11/2026, 31 days
START, END = date(2026, 10, 26), date(2026, 11, 25)


def emp(code, name="คน ทดสอบ", **kw):
    return {"id": code, "code": code, "full_name": name, "nickname": "", "branch": "สระบุรี", "position": "ผลิต", "status": "active", "start_date": None,
            "end_date": None, "pay_group": "company", "probation_end": None, **kw}


def rule(code, group="DAY", **kw):
    return {"employee_id": code, "pay_group": group, "commission_pct": None, "bag_rate": None, "ot_rate": None, "ot_auto_hours": None, "bonus": False,
            "rate2": None, "rides_with": None, "route_until": None, **kw}


def rate(code, baht=390, since="2026-08-26", **kw):
    return {"id": f"r-{code}", "employee_id": code, "daily_rate": baht, "effective_from": since, "kind": "normal", "review": False, "review_note": None, **kw}


def prev(code, deductions=None, parts=None, sales=None, month="2026-10"):
    """A row of an HQ pay sheet (the one of the month before, unless `month` says otherwise)."""
    return {"month": month, "code": code, "employee_id": code, "parts": parts or {}, "deductions": deductions or {}, "sales": sales, "refund": 0, "flags": [],
            "savings_balance": None}


ADVANCE_IN = [{"batch": "2026-11-saraburi", "feed": "midmonth", "branch": "สระบุรี", "kind": "advance", "page": 1},
              {"batch": "2026-11-kk", "feed": "midmonth", "branch": "แก่งคอย", "kind": "advance", "page": 1}]      # both branches' mid-month lists are booked


def drove(code, first, last, money=1000, **kw):
    """A row a day from `first` to `last` (inclusive)."""
    return [{"employee_id": code, "day": (first + timedelta(days=i)).isoformat(), "qty": {"zong": 2, "lod": 10}, "money": money, "spare": False, "money_parts": None, **kw}
            for i in range((last - first).days + 1)]


def src(**kw):
    base = {"employees": [], "rules": [], "rates": [], "pay_prev": [], "pay_tail": [], "route": [], "split": [], "punches": [], "loans": [], "loan_entries": [],
            "card_fuel": [], "cash_fuel": [], "vehicles": [], "drivers": [], "trips": [], "docs": [], "inbox": [], "penalties": [], "failed": []}
    return {**base, **kw}


def one(out, code):
    return next(p for p in out["people"] if p["code"] == code)


def kind(out, key):
    return next(k for k in out["kinds"] if k["key"] == key)


def file(out, key):
    return next(f for f in out["files"] if f["key"] == key)


# --------------------------------------------------------------------------------------------- the pay month
def test_the_month_to_open_on():
    assert pw.default_month(date(2026, 11, 26)) == "2026-11"            # the month that just closed: the one being paid
    assert pw.default_month(date(2026, 12, 1)) == "2026-11"
    assert pw.default_month(date(2026, 12, 2)) == "2026-12"             # from the 2nd on, the running one
    assert pw.default_month(date(2026, 11, 25)) == "2026-11"
    ms = pw.months(date(2026, 11, 26))
    assert ms[:3] == ["2026-12", "2026-11", "2026-10"] and len(ms) == 13 and ms[-1] == "2025-12"
    f = pw.frame(MONTH, TODAY)
    assert (f["start"], f["end"], f["days"], f["closed"]) == ("2026-10-26", "2026-11-25", 31, True)
    assert pw.frame("2026-12", TODAY)["closed"] is False
    assert pw.my("2026-10") == "10/2026" and pw.half_labels(START, END) == {1: "26/10-10/11", 2: "11/11-25/11"}


@pytest.mark.parametrize("bad", ["2026-13", "11/2026", "2569-11x", "abc", "2026-1"])
def test_a_bad_month_is_refused(bad):
    with pytest.raises(pw.PayrollError, match="เดือนไม่ถูกต้อง"):
        pw.parse_month(bad)


def test_a_month_not_reached_yet_is_refused():
    with pytest.raises(pw.PayrollError, match="ยังไม่ถึง"):
        pw.load("2027-01", TODAY)
    with pytest.raises(pw.PayrollError, match="ย้อนหลัง"):                # older than the 13 months on offer
        pw.load("2025-11", TODAY)


def test_dates_in_a_tight_place():
    d = lambda day: date(2026, 11, day)  # noqa: E731
    assert pw.runs([d(22), d(23), d(24), d(25)]) == "22/11-25/11"
    assert pw.runs([d(24), d(25)]) == "24/11, 25/11"
    assert pw.runs([d(25)]) == "25/11"
    assert pw.runs([d(3), d(10), d(11), d(12), d(20)]) == "03/11, 10/11-12/11, 20/11"
    assert pw.dmy(d(5)) == "05/11/2026"


def test_a_person_is_written_code_then_name():
    assert pw.who({"code": "SB-PD004", "full_name": "มานัส ศรีวิลาส", "nickname": "มานัส"}) == "SB-PD004 มานัส ศรีวิลาส"       # the nickname says nothing more
    assert pw.who({"code": "SB-PD014", "full_name": "ศุภกร  มูลเซอร์", "nickname": "ไมค์"}) == "SB-PD014 ศุภกร มูลเซอร์ (ไมค์)"
    assert pw.truck_label({"vehicle_number": "27", "plate_number": "84-2682", "vehicle_class": "รถใหญ่", "branch": "สระบุรี"}) == "#27 84-2682 รถใหญ่ สระบุรี"


# --------------------------------------------------------------------------------------------- route drivers
def route_src(rows, **kw):
    return src(**{"employees": [emp("D1", "มานัส ศรีวิลาส", position="พนักงานขับรถสาย")], "rules": [rule("D1", "DRV-A", commission_pct=5)],
                  "rates": [rate("D1", 120)], "pay_prev": [prev("D1")], "route": rows, **kw})


def test_a_route_driver_with_every_day_typed():
    out = pw.build(route_src(drove("D1", START, END), docs=ADVANCE_IN), MONTH, TODAY)
    p = one(out, "D1")
    assert p["cells"]["days"] == {"state": "ok", "value": 31, "text": "31 วัน", "note": ""}
    assert p["cells"]["sales"]["state"] == "ok" and p["cells"]["sales"]["value"] == 31000 and p["cells"]["sales"]["text"] == "31,000"
    assert p["cells"]["bags"]["state"] == "na" and p["cells"]["fuel"]["state"] == "na"        # no baht per ซอง, not DRV-D
    assert p["state"] == "ok" and p["missing"] == [] and (p["group"], p["group_label"], p["rate"]) == ("DRV-A", "พขร. รถหลอดใหญ่ 120/วัน + 5%", 120)
    assert file(out, "route_sales")["state"] == "ok"


def test_days_not_typed_yet_are_missing():
    out = pw.build(route_src(drove("D1", START, date(2026, 11, 21))), MONTH, TODAY)
    p = one(out, "D1")
    assert p["cells"]["days"] == {"state": "miss", "value": 27, "text": "27 วัน", "note": "ยังไม่ได้ลง 22/11-25/11"}
    assert p["cells"]["sales"]["state"] == "miss" and p["cells"]["sales"]["value"] == 27000 and "ยังขาด 4 วัน" in p["cells"]["sales"]["note"]
    assert p["state"] == "miss" and p["missing"] == ["วันทำงาน", "ยอดขาย"]
    f = file(out, "route_sales")
    assert (f["state"], f["summary"], f["link"]) == ("bad", "ยังไม่ได้ลง 4 วัน ของ 1 คน\nD1 มานัส ศรีวิลาส: 22/11-25/11", "#driver-days")      # who and which dates
    k = kind(out, "days")
    assert (k["state"], k["need"], k["ok"], k["miss"]) == ("bad", 1, 0, 1)
    assert k["rows"] == [{"who": "D1 มานัส ศรีวิลาส", "detail": "27 วัน · ยังไม่ได้ลง 22/11-25/11", "state": "bad"}]
    assert (k["link"], k["link_label"]) == ("#attendance", "เวลาทำงาน")           # most people's days come from the scans, not from the sales rows


def test_a_day_the_spare_drove_is_a_day_off():
    rows = drove("D1", START, END)
    for r in rows[5:7]:
        r.update(spare=True, money=9999)                              # the spare's sales count for nobody
    p = one(pw.build(route_src(rows), MONTH, TODAY), "D1")
    assert p["cells"]["days"] == {"state": "ok", "value": 29, "text": "29 วัน", "note": "หยุด 2 วัน (สำรองขับ)"}
    assert p["cells"]["sales"]["value"] == 29000 and p["cells"]["sales"]["state"] == "ok"


def test_a_route_that_ended_inside_the_period():
    """Truck 9 (owner, 26 Nov 2026): route days only to 17/11; the days after are other work with no source -- missing, but not route sales."""
    s = route_src(drove("D1", START, date(2026, 11, 17)))
    s["rules"][0].update(pay_group="DRV-E", route_until="2026-11-17")
    out = pw.build(s, MONTH, TODAY)
    p = one(out, "D1")
    assert p["cells"]["days"]["state"] == "miss" and p["cells"]["days"]["value"] == 23
    assert p["cells"]["days"]["note"] == "18/11-25/11 ทำงานอื่น ยังไม่มีข้อมูลวันทำงาน"
    assert p["cells"]["sales"]["state"] == "ok" and p["missing"] == ["วันทำงาน"]
    assert file(out, "route_sales")["state"] == "ok"                                          # his 8 days are not untyped sales
    assert any("17/11/2026" in d["detail"] for d in out["decisions"])


def test_a_route_that_ended_before_the_period():
    """SB-PD011 seen in 2026-12: the route ended 17/11, before the period's first day (26/11). No route days, no sales, no range
    reaching back before the period -- and nothing green."""
    s = route_src(drove("D1", date(2026, 11, 26), date(2026, 11, 28)))
    s["rules"][0].update(pay_group="DRV-E", route_until="2026-11-17", bag_rate=3)
    out = pw.build(s, "2026-12", date(2026, 12, 20))
    c = one(out, "D1")["cells"]
    assert c["days"] == {"state": "miss", "value": None, "text": "", "note": "เลิกขับรถสายตั้งแต่ 17/11/2026 · ยังไม่มีสแกนนิ้ว / ใบสรุปวันหยุด"}
    assert c["sales"]["state"] == "na" and c["bags"]["state"] == "na" and c["fuel"]["state"] == "na"
    assert kind(out, "sales")["need"] == 0 and file(out, "route_sales")["summary"].startswith("ลงครบ 0 คน")
    titles = [d["title"] for d in out["decisions"]]
    assert any(t.startswith("เลิกขับรถสายแล้ว") for t in titles) and not any("ระหว่างงวด" in t for t in titles)
    assert any("17/11/2026" in d["detail"] and "DRV-E" in d["detail"] for d in out["decisions"])


def test_the_first_day_of_a_period_expects_nothing_yet():
    """26/11/2026, the first day of 2026-12: yesterday belongs to the month before, so no driver is behind."""
    s = route_src([])
    s["employees"].append(emp("H1", "เย ทดสอบ", position="เด็กรถ"))
    s["rules"] += [rule("H1", rides_with="D1")]
    s["rules"][0].update(bag_rate=3)
    out = pw.build(s, "2026-12", date(2026, 11, 26))
    c = one(out, "D1")["cells"]
    assert c["days"] == {"state": "carry", "value": 0, "text": "0 วัน", "note": "งวดเพิ่งเริ่ม ยังไม่ถึงวันที่ต้องลง"}
    assert c["sales"]["state"] == "carry" and c["bags"]["state"] == "carry" and one(out, "D1")["missing"] == []
    assert one(out, "H1")["cells"]["days"]["state"] == "carry" and "งวดเพิ่งเริ่ม" in one(out, "H1")["cells"]["days"]["note"]
    f = file(out, "route_sales")
    assert (f["state"], f["summary"]) == ("info", "งวดเพิ่งเริ่ม ยังไม่ถึงวันที่ต้องลง") and out["closed"] is False
    # the day after, the 26th is expected
    assert one(pw.build(route_src([]), "2026-12", date(2026, 11, 27)), "D1")["cells"]["days"]["note"] == "ยังไม่ได้ลง 26/11"


def test_a_driver_who_types_sacks_and_tubes_apart():
    rows = drove("D1", START, END, money=500, money_parts={"zong": 300, "lod": 200})
    s = route_src(rows, split=[{"employee_id": "D1", "money_split": True}])
    s["rules"][0].update(pay_group="DRV-C", bag_rate=3)
    p = one(pw.build(s, MONTH, TODAY), "D1")
    assert p["cells"]["sales"]["value"] == 31 * 200 and "เฉพาะยอดหลอด" in p["cells"]["sales"]["note"]      # the หลอด part only
    assert p["cells"]["bags"] == {"state": "ok", "value": 62, "text": "62 ซอง", "note": ""}


def test_a_running_month_is_only_expected_up_to_yesterday():
    out = pw.build(route_src(drove("D1", date(2026, 11, 26), date(2026, 11, 27))), "2026-12", date(2026, 11, 29))
    assert one(out, "D1")["cells"]["days"]["note"] == "ยังไม่ได้ลง 28/11" and out["closed"] is False


# --------------------------------------------------------------------------------------------- everyone else's days
def test_a_helper_follows_his_driver():
    rows = drove("D1", START, date(2026, 11, 21))
    s = route_src(rows)
    s["employees"].append(emp("H1", "เย ทดสอบ", position="เด็กรถ"))
    s["rules"].append(rule("H1", rides_with="D1"))
    s["rates"].append(rate("H1"))
    s["pay_prev"].append(prev("H1"))
    c = one(pw.build(s, MONTH, TODAY), "H1")["cells"]["days"]
    assert (c["state"], c["value"]) == ("miss", 27)                                           # known to be short: his driver has days untyped
    assert c["note"] == "ตามคนขับ D1 มานัส ศรีวิลาส: 27 วัน · คนขับยังไม่ได้ลง 22/11-25/11"
    s["route"] = drove("D1", START, END)
    c = one(pw.build(s, MONTH, TODAY), "H1")["cells"]["days"]
    assert (c["state"], c["value"], c["note"]) == ("carry", 31, "ตามคนขับ D1 มานัส ศรีวิลาส: 31 วัน (ยังไม่ยืนยัน)")
    s["route"] = []                                                                           # the driver typed nothing at all: red, not an amber "0 วัน"
    c = one(pw.build(s, MONTH, TODAY), "H1")["cells"]["days"]
    assert (c["state"], c["value"]) == ("miss", 0) and "คนขับยังไม่ได้ลง 26/10-25/11" in c["note"]


def test_scans_are_a_stand_in_and_nothing_is_missing():
    punches = [{"employee_id": "SB-KK012", "punched_at": "2026-11-01T07:58:00"}, {"employee_id": "SB-KK012", "punched_at": "2026-11-01T20:01:00"},
               {"employee_id": "SB-KK012", "punched_at": "2026-11-02T08:00:00"}, {"employee_id": "SB-KK012", "punched_at": "2026-10-25T08:00:00"}]      # the 25th: last month's
    s = src(employees=[emp("SB-KK012", branch="แก่งคอย"), emp("N1"), emp("T1"), emp("SB-KK013", branch="แก่งคอย")],
            rules=[rule("SB-KK012", "KK-PROD"), rule("N1"), rule("T1", rate2=344), rule("SB-KK013", "KK-PROD")],
            rates=[rate("SB-KK012"), rate("N1"), rate("T1"), rate("SB-KK013")], punches=punches)
    out = pw.build(s, MONTH, TODAY)
    c = one(out, "SB-KK012")["cells"]
    assert c["days"] == {"state": "carry", "value": 2, "text": "2 วัน", "note": "มีสแกน 2 วัน ยังไม่ได้สรุปเป็นวันทำงาน"}
    assert (c["ot"]["state"], c["ot"]["note"]) == ("carry", "มีสแกน ยังไม่ได้สรุป OT")
    assert one(out, "N1")["cells"]["days"] == {"state": "miss", "value": None, "text": "", "note": "ยังไม่มีสแกนนิ้ว / ใบสรุปวันหยุด"}
    assert one(out, "N1")["cells"]["ot"]["state"] == "na"                                     # สระบุรี, no OT last month
    assert one(out, "SB-KK013")["cells"]["ot"] == {"state": "miss", "value": None, "text": "", "note": "ยังไม่มีชั่วโมง OT (ใบสรุปสิ้นเดือนแก่งคอย)"}
    two = one(out, "T1")
    assert two["cells"]["days"]["note"] == "ยังไม่มีสแกนนิ้ว / ใบสรุปวันหยุด · ต้องแยกวันออกรถ (344)"
    assert two["cells"]["rate"] == {"state": "carry", "value": 390, "text": "390 / 344", "note": "2 อัตรา: วันออกรถอย่างเดียว 344"} and two["rate2"] == 344
    f = file(out, "scan_kk")
    assert f["state"] == "warn" and "01/11/2026" in f["summary"] and "02/11/2026" in f["summary"]          # two days of a 31-day period
    assert file(out, "scan_sb_fr")["summary"] == "ยังไม่ได้อัปโหลดงวดนี้" and file(out, "scan_sb_pd")["state"] == "bad"


def test_a_full_scan_file_is_green():
    punches = [{"employee_id": "SB-KK001", "punched_at": f"{(START + timedelta(days=i)).isoformat()}T08:00:00"} for i in range(31)]
    out = pw.build(src(employees=[emp("SB-KK001", branch="แก่งคอย")], rules=[rule("SB-KK001")], punches=punches), MONTH, TODAY)
    assert (file(out, "scan_kk")["state"], file(out, "scan_kk")["summary"]) == ("ok", "1 คน 31 สแกน (26/10 ถึง 25/11)")


# --------------------------------------------------------------------------------------------- who is on the list
def test_who_is_on_the_list_and_their_status():
    people = [
        emp("SB-FR061", "ดูเรง", status="resigned", end_date="2026-11-25"),                   # the owner's case: worked the whole period
        emp("SB-FR200", start_date="2026-11-10"),
        emp("SB-KK073", branch="แก่งคอย", probation_end="2026-11-12"),
        emp("SB-FR005", pay_group="hq"),                                                     # HQ pays: counted, never listed
        emp("SB-FR095", status="resigned", end_date="2026-10-25"),                           # left before, savings still owed
        emp("SB-FR016", status="resigned", end_date="2026-10-02"),                           # left before, refunded
        emp("SB-FR110", status="resigned", end_date="2026-09-28"),                           # left before, never had savings
        emp("SB-FR300", start_date="2026-11-27"),                                            # starts next period
        emp("SB-FR001"),
    ]
    tail = [{"month": "2026-10", "code": "SB-FR061", "employee_id": "SB-FR061", "refund": 0, "savings_balance": 10000},
            {"month": "2026-10", "code": "SB-FR095", "employee_id": "SB-FR095", "refund": 0, "savings_balance": 2500},
            {"month": "2026-10", "code": "SB-FR016", "employee_id": "SB-FR016", "refund": 10000, "savings_balance": 0},
            {"month": "2026-09", "code": "SB-FR016", "employee_id": "SB-FR016", "refund": 0, "savings_balance": 10000},
            {"month": "2026-08", "code": "SB-FR110", "employee_id": "SB-FR110", "refund": 0, "savings_balance": 0}]
    out = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people], rates=[rate(p["id"]) for p in people], pay_tail=tail), MONTH, TODAY)
    assert [p["code"] for p in out["people"]] == ["SB-FR001", "SB-FR061", "SB-FR200", "SB-KK073", "SB-FR095"]      # สระบุรี, แก่งคอย, then the refund-only rows
    # the headline counts the people being paid: the one who left earlier and only waits for savings is beside it, not in it
    assert out["summary"] == {"people": 4, "ok": 0, "carry": 0, "miss": 4, "hq_paid": 1, "worked": 4, "left_before": 1}
    assert file(out, "start_dates")["summary"].startswith("3 คนยังไม่มีวันเริ่มงาน") and file(out, "start_dates")["state"] == "info"
    assert one(out, "SB-FR061")["status"] == {"key": "resigned", "label": "ลาออก", "detail": "วันสุดท้าย 25/11/2026"}
    assert one(out, "SB-FR061")["cells"]["refund"] == {"state": "miss", "value": 10000, "text": "10,000", "note": "สะสม 10,000 บาท ยังไม่ได้คืน"}
    assert one(out, "SB-FR200")["status"] == {"key": "new", "label": "พนักงานใหม่", "detail": "เริ่ม 10/11/2026"}
    assert one(out, "SB-KK073")["status"]["key"] == "probation" and one(out, "SB-KK073")["status"]["label"] == "ทดลองงาน"
    assert one(out, "SB-FR001")["status"] == {"key": "working", "label": "ทำงาน", "detail": ""}
    gone = one(out, "SB-FR095")
    assert gone["status"]["key"] == "left_before" and gone["status"]["label"] == "ลาออกแล้ว" and gone["status"]["detail"].startswith("รอคืนเงินสะสม")
    assert [k for k, c in gone["cells"].items() if c["state"] != "na"] == ["refund"] and gone["missing"] == ["คืนเงินสะสม"]
    assert gone["cells"]["refund"]["note"].startswith("สะสม 2,500 บาท ยังไม่ได้คืน")
    assert set(gone["cells"]) == set(pw.KEYS) == {c["key"] for c in out["columns"]}                                # every column key present


def test_probation_by_the_rate_in_force():
    out = pw.build(src(employees=[emp("A")], rules=[rule("A")], rates=[rate("A", 357, kind="probation")]), MONTH, TODAY)
    assert one(out, "A")["status"]["key"] == "probation"


def test_savings_owed_reads_the_latest_known_balance():
    # a later sheet with no balance printed and no refund: only the last printed figure, to be checked -- never a firm debt
    assert pw.savings_owed([{"month": "2026-08", "refund": 0, "savings_balance": None}, {"month": "2026-07", "refund": 0, "savings_balance": 12000}]) == \
        {"balance": 12000, "month": "2026-07", "refunded": False, "unsure": True, "last": "2026-08", "said": ""}
    assert pw.savings_owed([{"month": "2026-09", "refund": 0, "savings_balance": 4000}, {"month": "2026-08", "refund": 0, "savings_balance": 4000}])["unsure"] is False
    assert pw.savings_owed([{"month": "2026-09", "refund": 20500, "savings_balance": None}, {"month": "2026-08", "refund": 0, "savings_balance": 20500}])["refunded"] is True
    assert pw.savings_owed([{"month": "2026-07", "refund": 10000, "savings_balance": 10000}])["refunded"] is True
    assert pw.savings_owed([{"month": "2026-07", "refund": 0, "savings_balance": None}]) is None


def test_savings_paid_back_inside_another_column_are_not_owed():
    """SB-PD046: his 08/2026 sheet paid the 12,000 back in ซอง% (set off against a loan) and printed no balance; the refund column is 0."""
    rows = [{"month": "2026-08", "code": "SB-PD046", "employee_id": "SB-PD046", "refund": 0, "savings_balance": None, "bag": 12000, "other": 0,
             "flags": ["ซอง% 12,000 is far above normal bag pay: savings refund / back pay? counted as bag pay"]},
            {"month": "2026-07", "code": "SB-PD046", "employee_id": "SB-PD046", "refund": 0, "savings_balance": 12000, "bag": 0, "other": 0, "flags": []}]
    assert pw.savings_owed(rows)["refunded"] is True
    assert pw.savings_owed([{**rows[0], "bag": 900}, rows[1]])["refunded"] is False            # ordinary ซอง pay is not a refund
    assert pw.savings_owed([{**rows[0], "flags": []}, rows[1]])["refunded"] is False           # nor is a big figure nobody flagged
    gone = [emp("SB-PD046", status="resigned", end_date="2026-08-15"), emp("SB-KK065", status="resigned", end_date="2026-10-25"),
            emp("SB-FR077", status="resigned", end_date="2026-09-01"), emp("A")]
    tail = rows + [{"month": "2026-10", "code": "SB-KK065", "employee_id": "SB-KK065", "refund": 0, "savings_balance": 1500, "flags": ["หมายเหตุในชื่อ: ออกคืนสะสม01/12/69"]},
                   {"month": "2026-09", "code": "SB-FR077", "employee_id": "SB-FR077", "refund": 0, "savings_balance": None},
                   {"month": "2026-08", "code": "SB-FR077", "employee_id": "SB-FR077", "refund": 0, "savings_balance": 3000}]
    out = pw.build(src(employees=gone, rules=[rule("A")], pay_tail=tail), MONTH, TODAY)
    assert [p["code"] for p in out["people"]] == ["A", "SB-FR077", "SB-KK065"]                  # SB-PD046 is not on the list
    c = one(out, "SB-KK065")["cells"]["refund"]
    assert c["state"] == "miss" and c["note"] == "สะสม 1,500 บาท ยังไม่ได้คืน (ยอดตามใบ HQ 10/2026) · HQ หมายเหตุ: ออกคืนสะสม01/12/69"
    c = one(out, "SB-FR077")["cells"]["refund"]                                                # amber: to be checked
    assert c["state"] == "carry" and c["value"] == 3000 and "09/2026" in c["note"] and "08/2026" in c["note"] and "ตรวจว่าคืนแล้วหรือยัง" in c["note"]


# --------------------------------------------------------------------------------------------- rate
def test_the_rate_cell():
    people = [emp("A"), emp("B"), emp("C"), emp("D")]
    rates = [rate("A"), rate("C", 350, review=True, review_note="ให้ตรวจ"), rate("D", 400, since="2026-11-10"), rate("D", 380, since="2026-01-01")]
    out = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people], rates=rates), MONTH, TODAY)
    assert one(out, "A")["cells"]["rate"] == {"state": "ok", "value": 390, "text": "390", "note": ""}
    assert one(out, "B")["cells"]["rate"]["state"] == "miss" and one(out, "B")["rate"] is None
    assert one(out, "C")["cells"]["rate"]["state"] == "carry" and "ให้ตรวจ" in one(out, "C")["cells"]["rate"]["note"]
    d = one(out, "D")["cells"]["rate"]
    assert d["state"] == "carry" and d["value"] == 400 and "10/11/2026" in d["note"] and "380" in d["note"]            # a raise inside the period
    assert any(x["title"].startswith("ยังไม่มีอัตราค่าแรง: B ") for x in out["decisions"])


def test_a_person_without_a_group():
    out = pw.build(src(employees=[emp("A")], rates=[rate("A")]), MONTH, TODAY)
    assert one(out, "A")["cells"]["group"]["state"] == "miss" and kind(out, "group")["state"] == "bad"


# --------------------------------------------------------------------------------------------- deductions
def test_last_months_sheet_stands_in_for_the_deductions():
    people = [emp("A"), emp("B"), emp("C")]
    prevs = [prev("A", {"advance": 3000, "savings": 500, "sso": 429, "common": 350, "card": 0}), prev("B", {"advance": 0, "savings": 0, "sso": 0, "common": 0})]
    out = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people], rates=[rate(p["id"]) for p in people], pay_prev=prevs), MONTH, TODAY)
    a, b, c = (one(out, x)["cells"] for x in "ABC")
    assert a["advance"] == {"state": "carry", "value": 3000, "text": "3,000", "note": "ตามใบ HQ 10/2026 · รอใบเบิกกลางเดือน"}
    assert a["savings"] == {"state": "carry", "value": 500, "text": "500", "note": "ตามใบ HQ 10/2026"}
    assert a["sso"] == {"state": "carry", "value": 429, "text": "429", "note": "ตามใบ HQ 10/2026"}
    assert a["common"] == {"state": "miss", "value": None, "text": "", "note": "ใบ HQ 10/2026 หัก 350 บาท · เปลี่ยนทุกเดือน ต้องใส่ใหม่"}       # never carried
    assert all(b[k]["state"] == "na" for k in ("savings", "sso", "common"))                                           # a row with zeros: not needed
    # who draws an advance changes every month: "none last month" is not "none this month" while the list is not in
    assert b["advance"] == {"state": "carry", "value": 0, "text": "0", "note": "ใบ HQ 10/2026 ไม่ได้เบิก · รอใบเบิกกลางเดือน"}
    assert all(c[k] == {"state": "miss", "value": None, "text": "", "note": "ไม่มีชื่อในใบ HQ 10/2026 — ยังไม่รู้ว่าต้องหักหรือไม่"} for k in ("advance", "savings", "sso"))
    assert c["common"]["state"] == "na"
    k = kind(out, "advance")
    assert (k["need"], k["ok"], k["carry"], k["miss"], k["state"]) == (3, 0, 2, 1, "bad")
    assert [r["state"] for r in k["rows"]] == ["bad", "warn", "warn"] and k["rows"][0]["who"].startswith("C ")        # the worst first
    assert k["rows"][1] == {"who": "A คน ทดสอบ", "detail": "3,000 · ตามใบ HQ 10/2026 · รอใบเบิกกลางเดือน", "state": "warn"}      # the amount being carried shows
    assert (file(out, "prev_sheet")["state"], file(out, "prev_sheet")["summary"]) == ("ok", "ใช้ใบ 10/2026 (2 คน)")
    # the branch's list is booked: an advance is still only last sheet's figure (IceBox cannot read the list per person yet)
    listed = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people], rates=[rate(p["id"]) for p in people], pay_prev=prevs, docs=ADVANCE_IN), MONTH, TODAY)
    assert one(listed, "A")["cells"]["advance"]["note"] == "ตามใบ HQ 10/2026 · มีใบเบิกแล้ว แต่ระบบยังอ่านรายคนไม่ได้" and one(listed, "B")["cells"]["advance"]["state"] == "na"


def test_the_stand_in_is_each_persons_latest_sheet_not_only_last_months():
    """2026-12 seen on 26/11/2026: HQ's sheets end at 2026-10 and a 2026-11 one may never come (IceBox is taking over). SB-KK061 is
    on the 09/2026 sheet only."""
    people = [emp("A"), emp("K61", branch="แก่งคอย"), emp("N")]
    sheets = [prev("A", {"advance": 3000, "savings": 500, "sso": 429, "common": 59}, parts={"ot": 0}),
              prev("K61", {"advance": 3000, "savings": 500, "sso": 0}, parts={"ot": 1080}, month="2026-09"),
              prev("A", {"advance": 0, "savings": 1000}, month="2026-08"),                                           # an older row of A: never used
              prev("A", {"advance": 9999}, month="2027-01")]                                                         # a later month than the one asked for: ignored
    out = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people], rates=[rate(p["id"]) for p in people], pay_prev=sheets), "2026-12", date(2026, 12, 20))
    a, k, n = (one(out, x)["cells"] for x in ("A", "K61", "N"))
    assert (a["advance"]["state"], a["advance"]["value"], a["savings"]["value"], a["sso"]["note"]) == ("carry", 3000, 500, "ตามใบ HQ 10/2026")
    assert a["common"]["state"] == "miss" and "ใบ HQ 10/2026 หัก 59 บาท" in a["common"]["note"]
    assert (k["savings"]["state"], k["savings"]["note"]) == ("carry", "ตามใบ HQ 09/2026 (ไม่มีชื่อในใบ 10/2026)")
    assert k["sso"]["state"] == "na" and k["ot"]["state"] == "miss"                           # her own row says: no ปกส., OT is paid
    assert n["advance"] == {"state": "miss", "value": None, "text": "", "note": "ไม่มีชื่อในใบ HQ 10/2026 — ยังไม่รู้ว่าต้องหักหรือไม่"}
    f = file(out, "prev_sheet")
    assert (f["state"], f["summary"]) == ("info", "ยังไม่มีใบ 11/2026 — ใช้ใบ 10/2026 แทน (1 คน)")
    none = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people]), "2026-12", date(2026, 12, 20))
    assert one(none, "A")["cells"]["sso"]["note"] == "ไม่มีใบ HQ ของ 5 เดือนล่าสุดให้ใช้เป็นค่าแทน" and file(none, "prev_sheet")["state"] == "warn"


def test_a_month_hq_already_worked_out_uses_its_own_sheet():
    """2026-10: HQ's sheet of the month itself is in. Its deductions are the month's own figures (green), and the page says so."""
    people = [emp("A"), emp("D1", "มานัส ศรีวิลาส", position="พนักงานขับรถสาย")]
    sheets = [prev("A", {"advance": 3000, "savings": 500, "sso": 429, "common": 63, "other": 725, "shortage": 300}), prev("D1"),
              prev("A", {"advance": 1000}, month="2026-09")]
    tail = [{"month": r["month"], "code": r["code"], "employee_id": r["employee_id"], "refund": 0, "savings_balance": 0} for r in sheets]
    s = src(employees=people, rules=[rule("A"), rule("D1", "DRV-A")], rates=[rate("A"), rate("D1")], pay_prev=sheets, pay_tail=tail)
    out = pw.build(s, "2026-10", TODAY)
    c = one(out, "A")["cells"]
    assert c["advance"] == {"state": "ok", "value": 3000, "text": "3,000", "note": "ตามใบ HQ 10/2026 (HQ คิดงวดนี้แล้ว)"}
    assert [c[k]["state"] for k in ("savings", "sso", "common", "other", "penalty")] == ["ok"] * 5 and c["penalty"]["value"] == 300
    assert out["hq_done"] == 2 and file(out, "hq_sheet")["summary"] == "มีแล้ว 2 คน" and "prev_sheet" not in [f["key"] for f in out["files"]]
    f = file(out, "route_sales")                                                              # nothing to type any more: information, not a to-do
    assert f["state"] == "info" and f["summary"] == "งวดนี้ HQ คิดแล้ว — ในระบบไม่มียอดขายรายวัน 30 วัน ของ 1 คน"
    assert all(k["note"] == "" for k in out["kinds"]) and kind(out, "nobonus")["state"] == "na"


def test_last_months_row_is_found_by_code_when_it_has_no_person():
    row = {"month": "2026-10", "code": "sb-kk073", "employee_id": None, "parts": {"commission": 9000}, "deductions": {"advance": 3000}, "sales": None}
    e = {**emp("uuid-1", branch="แก่งคอย"), "code": "SB-KK073"}
    c = one(pw.build(src(employees=[e], rules=[rule("uuid-1", "DAY")], rates=[rate("uuid-1")], pay_prev=[row]), MONTH, TODAY), "SB-KK073")["cells"]
    assert c["advance"]["state"] == "carry" and c["trip"]["state"] == "miss" and c["trip"]["note"] == "ยังไม่มีตารางเที่ยวงวดนี้"      # a truck helper paid by the trip


def test_sales_trip_money_and_ot_follow_what_was_paid_last_month():
    e = [emp("K4", branch="แก่งคอย"), emp("K5", branch="แก่งคอย"), emp("S9"), emp("K6", branch="แก่งคอย")]
    prevs = [prev("K4", parts={"commission": 3143}, sales=157130), prev("K5", parts={"ot": 480}), prev("S9", parts={"ot": 120, "commission": 50}),
             prev("K6", parts={"commission": 8000})]
    s = src(employees=e, rules=[rule("K4", "KK-DRV"), rule("K5"), rule("S9"), rule("K6", "KK-DRV")], rates=[rate(x["id"]) for x in e], pay_prev=prevs,
            trips=[{"id": 1, "employee_code": "K6", "written_total": 8300, "computed_total": 8300}])
    out = pw.build(s, MONTH, TODAY)
    k4 = one(out, "K4")["cells"]
    assert k4["sales"]["state"] == "miss" and k4["sales"]["note"] == "ยังไม่มีแหล่งข้อมูล (ใบ HQ 10/2026 คิดจากยอดขาย 157,130)"
    assert k4["trip"]["state"] == "na"                                                        # paid a % of sales (SB-KK004): asked as ยอดขาย, never as ค่าเที่ยว too
    d = next(x for x in out["decisions"] if x["title"].startswith("ค่าคอมจากยอดขาย: K4 "))
    assert "3,143" in d["detail"] and "157,130" in d["detail"] and "ประมาณ 2%" in d["detail"]
    k6 = one(out, "K6")["cells"]
    assert (k6["trip"]["state"], k6["trip"]["value"], k6["sales"]["state"]) == ("carry", 8300, "na")
    assert one(out, "K5")["cells"]["ot"]["state"] == "miss" and one(out, "K5")["cells"]["trip"]["state"] == "na"
    assert one(out, "S9")["cells"]["ot"]["state"] == "na" and one(out, "S9")["cells"]["trip"]["state"] == "na"         # สระบุรี: OT is paid in cash, daily
    assert file(out, "kk_trips")["state"] == "ok" and kind(out, "sales")["need"] == 1


def test_a_penalty_letter_dated_inside_the_period():
    docs = [{"id": 1, "employee_id": "A", "doc_date": "2026-11-03", "status": "signed", "penalty": 500},
            {"id": 2, "employee_id": "A", "doc_date": "2026-11-20", "status": "cancelled", "penalty": 900},
            {"id": 3, "employee_id": "B", "doc_date": "2026-11-05", "status": "signed", "penalty": None}]
    out = pw.build(src(employees=[emp("A"), emp("B")], rules=[rule("A"), rule("B")], penalties=docs), MONTH, TODAY)
    assert one(out, "A")["cells"]["penalty"] == {"state": "ok", "value": 500, "text": "500", "note": "ใบเตือน 03/11/2026 500"}
    assert one(out, "B")["cells"]["penalty"]["state"] == "na"


# --------------------------------------------------------------------------------------------- loans
def loan(code, **kw):
    return {"id": f"L-{code}", "employee_id": code, "kind": "loan", "amount": 6000, "loan_date": "2026-10-01", "reason": "ยอดค้างยกมา", "repay_mode": "monthly_fixed",
            "repay_amount": 1500, "repay_months": None, "repay_percent": None, "schedule": None, "first_month": "2026-10-01", "status": "active", **kw}


def paid(code, month, amount=1500):
    return {"id": f"E-{code}-{month}", "loan_id": f"L-{code}", "entry_date": f"{month}-25", "kind": "repay", "amount": amount, "pay_month": f"{month}-01",
            "created_at": f"{month}-25T00:00:00"}


def test_loans_come_from_the_schedule_and_the_card_column_is_never_carried_too():
    people = [emp(c) for c in "ABCDEF"]
    loans = [loan("A"), loan("B"), loan("C", repay_mode="flexible", repay_amount=None, amount=5000), loan("D", first_month="2026-12-01"),
             loan("E", kind="accident", amount=3500, repay_amount=2000), loan("F", status="cancelled")]
    entries = [paid("A", "2026-10"), paid("B", "2026-10"), paid("B", "2026-11"), paid("C", "2026-10", 2000), paid("E", "2026-10", 2000)]
    prevs = [prev("A", {"card": 1500, "loan": 0, "advance": 0}), prev("B", {"loan": 1500}), prev("C", {"loan": 2000}), prev("D"), prev("E", {"other": 2000}), prev("F")]
    s = src(employees=people, rules=[rule(c) for c in "ABCDEF"], rates=[rate(c) for c in "ABCDEF"], pay_prev=prevs, loans=loans, loan_entries=entries)
    out = pw.build(s, MONTH, TODAY)
    a = one(out, "A")["cells"]
    assert a["loan"] == {"state": "carry", "value": 1500, "text": "1,500", "note": 'ทำบัตร / passport: 1,500 ตามตาราง — ยังไม่ได้กด "หักแล้ว"'}
    assert [k for k, c in a.items() if c["value"] == 1500] == ["loan"]                        # last month's ทำบัตร IS this loan: one place only
    assert "card" not in pw.KEYS
    assert one(out, "B")["cells"]["loan"] == {"state": "ok", "value": 1500, "text": "1,500", "note": "1,500 หักแล้ว"}
    c = one(out, "C")["cells"]["loan"]
    assert (c["state"], c["value"], c["note"]) == ("miss", None, "ต้องใส่ยอด (ผ่อนไม่ตายตัว) คงเหลือ 3,000")
    assert one(out, "D")["cells"]["loan"]["state"] == "na"                                    # starts next month
    e = one(out, "E")["cells"]["loan"]
    assert (e["state"], e["value"]) == ("carry", 1500) and e["note"].startswith("อุบัติเหตุ: 1,500 ตามตาราง")           # the last instalment is what is left
    assert one(out, "F")["cells"]["loan"]["state"] == "na"
    k = kind(out, "loan")
    assert (k["need"], k["ok"], k["carry"], k["miss"], k["state"]) == (4, 1, 2, 1, "bad")
    f = file(out, "loans_mark")
    assert f["state"] == "warn" and f["summary"] == "ยังไม่ได้กด 2 รายการ 3,000 บาท (กดแล้ว 1) · ผ่อนไม่ตายตัว 1 รายการ ต้องใส่ยอดเอง"


def test_an_instalment_still_unmarked_from_an_earlier_month_is_said():
    """2026-12 seen before 11/2026 is pressed: SB-PD033's schedule ended in 11/2026 -- nothing is due in 12, but 1,500 is behind."""
    people = [emp("A"), emp("B")]
    loans = [loan("A", amount=3000), loan("B", amount=6000)]             # A: 10 + 11 and done; B: 10, 11, 12, 01
    entries = [paid("A", "2026-10"), paid("B", "2026-10")]
    out = pw.build(src(employees=people, rules=[rule("A"), rule("B")], loans=loans, loan_entries=entries), "2026-12", date(2026, 12, 20))
    a, b = one(out, "A")["cells"]["loan"], one(out, "B")["cells"]["loan"]
    assert (a["state"], a["value"], a["text"]) == ("carry", 1500, "1,500")                    # never an amber "0"
    assert a["note"] == 'งวดนี้ไม่มีตามตาราง · ค้างจากเดือนก่อน 1,500 — ยังไม่ได้กด "หักแล้ว"'
    assert (b["value"], b["note"]) == (1500, '1,500 ตามตาราง · ค้างจากเดือนก่อน 1,500 — ยังไม่ได้กด "หักแล้ว"')      # this month's figure; the older one beside it
    f = file(out, "loans_mark")
    assert (f["state"], f["summary"]) == ("warn", "ยังไม่ได้กด 1 รายการ 1,500 บาท · ค้างจากเดือนก่อน 2 รายการ 3,000 บาท")


def test_loans_all_marked_or_none_due():
    s = src(employees=[emp("B")], rules=[rule("B")], loans=[loan("B")], loan_entries=[paid("B", "2026-10"), paid("B", "2026-11")])
    assert file(pw.build(s, MONTH, TODAY), "loans_mark")["state"] == "ok"
    assert file(pw.build(src(employees=[emp("B")], rules=[rule("B")]), MONTH, TODAY), "loans_mark")["state"] == "info"


def test_the_loan_reading_matches_the_loans_page(monkeypatch):
    """loan_dues is loans.month_deductions with the columns named: the same due amount per loan."""
    from app import employees, loans
    ls = [loan("A"), loan("E", kind="accident", amount=3500, repay_amount=2000)]
    es = [paid("A", "2026-10"), paid("E", "2026-10", 2000)]
    monkeypatch.setattr(loans, "_require", lambda: None)
    monkeypatch.setattr(loans, "_all_loans", lambda: ls)
    monkeypatch.setattr(loans, "_entries_by_loan", lambda: {"L-A": [es[0]], "L-E": [es[1]]})
    monkeypatch.setattr(loans, "_who", lambda: {})
    monkeypatch.setattr(employees, "today_th", lambda: TODAY)
    theirs = {r["employee_id"]: r["due"] for r in loans.month_deductions(MONTH)["rows"]}
    ours = {pid: sum(i["amount"] for i in items) for pid, items in pw.loan_dues(ls, es, MONTH).items()}
    assert ours == theirs == {"A": 1500.0, "E": 1500.0}


# --------------------------------------------------------------------------------------------- fuel and the files
def fuel_src(**kw):
    s = route_src(drove("D1", START, END), vehicles=[{"id": "V27", "vehicle_number": "27", "plate_number": "84-2682", "vehicle_class": "รถใหญ่", "branch": "สระบุรี",
                                                     "usage": "รถสาย", "main_driver_id": "drv1", "active": True}],
                  drivers=[{"id": "drv1", "name": "มานัส  ศรีวิลาส"}], **kw)
    s["rules"][0].update(pay_group="DRV-D", bag_rate=3)
    return s


def test_fuel_is_asked_only_of_the_sack_driver():
    line = {"id": 1, "batch": 1, "amount": 800, "counted": True, "dup_of": None, "vehicle_id": "V27", "page_id": 7, "page": {"confirmed": True}}
    out = pw.build(fuel_src(cash_fuel=[line]), MONTH, TODAY)
    c = one(out, "D1")["cells"]["fuel"]
    assert c["state"] == "miss" and c["note"] == "ยังไม่มีใบแจ้งหนี้บัตรน้ำมันงวดนี้ (รถ #27 84-2682 รถใหญ่ สระบุรี) · น้ำมันเงินสด: 26/10-10/11 มีแล้ว · 11/11-25/11 ยังไม่มี"
    assert file(out, "card_fuel")["state"] == "bad"
    f = file(out, "cash_fuel")
    assert (f["state"], f["summary"]) == ("warn", "26/10-10/11 ยืนยันแล้ว 800 บาท · 11/11-25/11 ยังไม่ได้อัปโหลด")        # a half by its dates, never "งวด 26-10"
    card = [{"id": "e1", "source_id": "fuel:bangchak:2026-11:842682", "lines": [{"vehicle_id": "V27", "credit": 0}, {"vehicle_id": "V27", "credit": 29740}]},
            {"id": "e2", "source_id": "fuel:pt:2026-11:xx", "lines": [{"vehicle_id": "V9", "credit": 1000}]}]
    out = pw.build(fuel_src(cash_fuel=[line, {**line, "id": 2, "batch": 2, "amount": 200}], card_fuel=card), MONTH, TODAY)
    c = one(out, "D1")["cells"]["fuel"]
    assert (c["state"], c["value"], c["text"]) == ("ok", 30740, "30,740") and "บัตร 29,740 + เงินสด 1,000" in c["note"]
    assert (file(out, "card_fuel")["state"], file(out, "card_fuel")["summary"]) == ("ok", "บางจาก 1 คัน 29,740 บาท · PT 1 คัน 1,000 บาท")
    assert file(out, "cash_fuel")["state"] == "ok"
    assert kind(out, "fuel")["need"] == 1


def test_cash_fuel_counts_only_what_was_confirmed():
    v = pw.cash_fuel_view([{"batch": 1, "amount": 500, "counted": True, "dup_of": None, "vehicle_id": "V", "page_id": 1, "page": {"confirmed": False}},
                           {"batch": 2, "amount": 300, "counted": True, "dup_of": None, "vehicle_id": "V", "page_id": None, "page": None},      # typed in
                           {"batch": 2, "amount": 300, "counted": True, "dup_of": 5, "vehicle_id": "V", "page_id": 2, "page": {"confirmed": True}},
                           {"batch": 2, "amount": 100, "counted": False, "dup_of": None, "vehicle_id": "V", "page_id": 2, "page": {"confirmed": True}}])
    assert v["halves"][1]["state"] == "pending" and v["halves"][2] == {"state": "ok", "total": 300, "pending": 0} and v["by_vehicle"] == {"V": 300}
    out = pw.build(src(employees=[emp("A")], rules=[rule("A")]), MONTH, TODAY)
    assert (file(out, "cash_fuel")["state"], file(out, "cash_fuel")["summary"]) == ("bad", "ยังไม่ได้อัปโหลดทั้ง 2 ช่วง (26/10-10/11, 11/11-25/11)")


def test_the_branch_papers_and_the_order_of_the_files():
    docs = [{"batch": "2026-11-saraburi", "feed": "midmonth", "branch": "สระบุรี", "kind": "advance", "page": 3},
            {"batch": "2026-11-saraburi", "feed": "midmonth", "branch": "สระบุรี", "kind": "advance", "page": 4},
            {"batch": "docs:2026-11:แก่งคอย:end", "feed": "endmonth", "branch": "แก่งคอย", "kind": "expense", "page": 1}]
    tail = [{"month": "2026-11", "code": "A", "employee_id": "A", "refund": 0, "savings_balance": 0}]
    out = pw.build(src(employees=[emp("A")], rules=[rule("A")], docs=docs, pay_tail=tail), MONTH, TODAY)
    assert [f["key"] for f in out["files"]] == ["route_sales", "scan_sb_pd", "scan_sb_fr", "scan_kk", "advance_list", "month_papers", "card_fuel", "cash_fuel",
                                                "kk_trips", "loans_mark", "hq_sheet", "start_dates"]
    assert (file(out, "advance_list")["state"], file(out, "advance_list")["summary"]) == ("warn", "สระบุรี 2 หน้า · ยังไม่มี: แก่งคอย")
    assert (file(out, "month_papers")["state"], file(out, "month_papers")["summary"]) == ("warn", "แก่งคอย 1 หน้า · ยังไม่มี: สระบุรี")
    assert (file(out, "hq_sheet")["state"], file(out, "hq_sheet")["summary"]) == ("ok", "มีแล้ว 1 คน")
    empty = pw.build(src(employees=[emp("A")], rules=[rule("A")]), MONTH, TODAY)
    assert (file(empty, "month_papers")["state"], file(empty, "month_papers")["summary"]) == ("bad", "ยังไม่ได้อัปโหลด")
    assert (file(empty, "hq_sheet")["state"], file(empty, "hq_sheet")["summary"]) == ("info", "ยังไม่มี (ใช้เทียบผลเท่านั้น)")
    assert file(empty, "kk_trips")["state"] == "bad" and file(empty, "kk_trips")["link"] is None and "ต้องแจ้งให้ลงให้" in file(empty, "kk_trips")["summary"]
    assert [f["key"] for f in empty["files"]][-3:] == ["prev_sheet", "hq_sheet", "start_dates"]
    # a file in the drop box counts as waiting only while nobody has finished it: one kept on purpose ("stored") is not
    box = [{"id": 1, "kind": "scan", "status": "stored", "month": MONTH, "branches": None}, {"id": 2, "kind": "scan", "status": "needs_choice", "month": MONTH, "branches": None},
           {"id": 3, "kind": "scan", "status": "failed", "month": MONTH, "branches": None}, {"id": 4, "kind": "payroll", "status": "booked", "month": MONTH, "branches": None}]
    boxed = pw.build(src(employees=[emp("A")], rules=[rule("A")], inbox=box), MONTH, TODAY)
    assert file(boxed, "month_papers")["summary"] == "ยังไม่ได้อัปโหลด · มี 1 ไฟล์สแกนในกล่องรับไฟล์ ยังไม่ได้ลง · อ่านไม่สำเร็จ 1 ไฟล์"
    assert file(boxed, "hq_sheet")["summary"] == "ยังไม่มี (ใช้เทียบผลเท่านั้น)"
    assert all(set(f) == {"key", "title", "state", "summary", "link", "link_label"} and f["state"] in ("ok", "warn", "bad", "info") for f in out["files"])


# --------------------------------------------------------------------------------------------- roll-ups, failures
def test_states_roll_up_by_person_and_by_kind():
    people = [emp("A"), emp("B"), emp("C", position="พนักงานขับรถสาย")]
    s = src(employees=people, rules=[rule("A"), rule("B"), rule("C", "DRV-A")], rates=[rate("A"), rate("B"), rate("C")],
            pay_prev=[prev("A", {"advance": 3000}), prev("B"), prev("C")], route=drove("C", START, END), docs=ADVANCE_IN,
            punches=[{"employee_id": "A", "punched_at": "2026-11-01T08:00:00"}])
    out = pw.build(s, MONTH, TODAY)
    assert [one(out, x)["state"] for x in "ABC"] == ["carry", "miss", "ok"]                   # miss beats carry beats ok
    assert out["summary"] == {"people": 3, "ok": 1, "carry": 1, "miss": 1, "hq_paid": 0, "worked": 3, "left_before": 0}
    states = {k["key"]: k["state"] for k in out["kinds"]}
    assert states["group"] == "ok" and states["days"] == "bad" and states["advance"] == "warn" and states["fuel"] == "na" and states["sales"] == "ok"
    assert [k["key"] for k in out["kinds"]] == pw.KEYS == [c["key"] for c in out["columns"]]
    assert all({"key", "title", "state", "need", "ok", "carry", "miss", "source", "rows", "more", "link", "link_label", "note", "later"} == set(k) for k in out["kinds"])
    assert len(out["decisions"]) <= 10 and all(set(d) == {"title", "detail"} for d in out["decisions"])


def test_the_kinds_follow_the_workbooks_columns_and_never_read_as_an_all_clear():
    # sheet คำนวณ: F วันทำงาน G ยอดขาย H ซอง I น้ำมัน J OT K ค่าเที่ยว L งดเบี้ยขยัน ... X สะสม Y เบิก Z เงินกู้ AA ส่วนกลาง AC เงินขาด AD อื่นๆ AE ปกส. AH คืนสะสม
    assert pw.KEYS == ["group", "rate", "days", "sales", "bags", "fuel", "ot", "trip", "nobonus", "savings", "advance", "loan", "common", "penalty", "other", "sso", "refund"]
    out = pw.build(src(employees=[emp("A")], rules=[rule("A")], rates=[rate("A")]), MONTH, TODAY)
    pen = kind(out, "penalty")                                                                # nobody has a letter, but the papers that would carry one are not in
    assert (pen["need"], pen["state"]) == (0, "warn") and "ยังไม่ได้อัปโหลดเอกสารสิ้นเดือน" in pen["note"] and "เงินขาดของคนขับ" in pen["later"]
    assert kind(out, "nobonus")["state"] == "warn" and kind(out, "other")["state"] == "warn" and kind(out, "common")["state"] == "warn"
    assert kind(out, "fuel")["state"] == "na" and kind(out, "fuel")["note"] == ""              # truly nobody: only the sack driver's fuel is asked
    assert kind(out, "savings")["later"] and kind(out, "advance")["later"] and not kind(out, "loan")["later"] and not kind(out, "sales")["later"]
    papers = [{"batch": f"docs:2026-11:{b}:end", "feed": "endmonth", "branch": b, "kind": "expense", "page": 1} for b in pw.BRANCHES]
    full = pw.build(src(employees=[emp("A")], rules=[rule("A")], rates=[rate("A")], docs=papers + ADVANCE_IN), MONTH, TODAY)
    assert (kind(full, "penalty")["state"], kind(full, "penalty")["note"]) == ("na", "") and "ยังไม่มีที่ลง" in kind(full, "common")["note"]


def test_people_with_the_same_line_are_folded_and_a_long_list_is_cut_at_eighty():
    """The real 2026-11: 107 people share "ยังไม่มีสแกนนิ้ว" and hid the four drivers with dates to chase. The specific lines come first."""
    people = [emp(f"P{i:03d}") for i in range(95)] + [emp("D1", "มานัส ศรีวิลาส", position="พนักงานขับรถสาย")]
    s = src(employees=people, rules=[rule(p["id"]) for p in people[:95]] + [rule("D1", "DRV-A")], route=drove("D1", START, date(2026, 11, 21)))
    k = kind(pw.build(s, MONTH, TODAY), "days")
    assert (k["miss"], len(k["rows"]), k["more"]) == (96, 2, 0)
    assert k["rows"][0] == {"who": "D1 มานัส ศรีวิลาส", "detail": "27 วัน · ยังไม่ได้ลง 22/11-25/11", "state": "bad"}
    assert k["rows"][1] == {"who": "95 คน", "detail": "ยังไม่มีสแกนนิ้ว / ใบสรุปวันหยุด", "state": "bad", "count": 95}
    s["rules"] = [rule(p["id"], rate2=300 + i) for i, p in enumerate(people[:95])] + [rule("D1", "DRV-A")]      # 96 different lines: nothing to fold
    k = kind(pw.build(s, MONTH, TODAY), "days")
    assert (k["miss"], len(k["rows"]), k["more"]) == (96, 80, 16) and k["rows"][0]["who"].startswith("D1 ")


def test_a_source_that_failed_shows_as_missing_and_is_named():
    s = route_src(None, failed=["ยอดขายรายวัน", "บัตรน้ำมัน"], card_fuel=None, punches=None, docs=None, loans=None, pay_prev=None, trips=None)
    out = pw.build(s, MONTH, TODAY)
    c = one(out, "D1")["cells"]
    assert c["days"]["state"] == "miss" and c["days"]["note"] == "โหลดยอดขายรายวันไม่สำเร็จ" and c["advance"]["note"] == "โหลดใบสรุปเงินเดือน HQ ไม่สำเร็จ"
    assert out["failed"] == ["ยอดขายรายวัน", "บัตรน้ำมัน"]
    assert all(file(out, k)["summary"] == "โหลดไม่สำเร็จ" for k in ("route_sales", "scan_kk", "advance_list", "card_fuel", "loans_mark", "kk_trips", "prev_sheet"))


def test_the_decisions_come_from_the_data():
    people = [emp("A"), emp("K", branch="แก่งคอย"), emp("D", position="พนักงานขับรถสาย"), emp("T")]
    rules = [rule("A", bonus=True), rule("K", "KK-PROD", bonus=True, ot_auto_hours=3), rule("D", "DRV-A", bonus=True), rule("T", rate2=344)]
    out = pw.build(src(employees=people, rules=rules, rates=[rate(p["id"]) for p in people], route=drove("D", START, END)), MONTH, TODAY)
    titles = [d["title"] for d in out["decisions"]]
    assert titles[0] == "เบี้ยขยัน: ใครได้บ้าง" and "(3 คน)" in out["decisions"][0]["detail"] and "2 คน" in out["decisions"][0]["detail"]
    assert titles[1] == "OT แก่งคอย: ใส่ชั่วโมงจริง หรือ 3 ชม. ทุกวัน"
    assert "กลุ่ม A, B, C" in titles[2] and any("2 อัตรา" in t for t in titles) and "สแกนนิ้วครั้งเดียว" in titles[-1]


# --------------------------------------------------------------------------------------------- the loader, the menu, the gate
def test_load_reads_once_a_minute_and_asks_for_the_schema(monkeypatch):
    calls = []
    monkeypatch.setattr(pw, "has_v93", lambda: True)
    monkeypatch.setattr(pw, "_read", lambda month: calls.append(month) or src(employees=[emp("A")], rules=[rule("A")]))
    pw.forget()
    a = pw.load("", TODAY)
    assert a["month"] == "2026-11" and a["ready"] is True and pw.load("2026-11", TODAY) is a and calls == ["2026-11"]            # kept
    assert pw.load("2026-11", TODAY, fresh=True) is not a and calls == ["2026-11", "2026-11"]                                    # fresh reads again
    pw.load("2026-10", TODAY)
    assert calls[-1] == "2026-10"
    monkeypatch.setattr(pw, "has_v93", lambda: False)
    out = pw.load("2026-11", TODAY)
    assert out["ready"] is False and out["need"] == "schema_v93.sql" and out["people"] == [] and out["months"][1] == "2026-11"
    pw.forget()


def test_the_loader_names_what_failed_and_keeps_the_rest(monkeypatch):
    from app import supabase

    def fake(table, params=None, page=1000):
        if table == "employees":
            return [emp("A")]
        if table in ("route_driver_days", "attendance_punches"):
            raise supabase.SupabaseError("boom")
        return []
    monkeypatch.setattr(supabase, "select_all", fake)
    s = pw._read(MONTH)
    assert s["failed"] == ["ยอดขายรายวัน", "สแกนนิ้ว"] and s["route"] is None and s["punches"] is None and s["loans"] == []
    assert pw.build(s, MONTH, TODAY)["summary"]["people"] == 1
    monkeypatch.setattr(supabase, "select_all", lambda table, params=None, page=1000: (_ for _ in ()).throw(supabase.SupabaseError("down")))
    with pytest.raises(supabase.SupabaseError):                       # no people list: nothing to show
        pw._read(MONTH)


def test_a_timeout_in_one_source_is_named_too(monkeypatch):
    """A timeout or a dropped connection is an httpx error, not a SupabaseError: it must land in "failed" like any other."""
    import httpx
    from app import main, supabase

    def fake(table, params=None, page=1000):
        if table == "fin_trip_tables" or (table == "employee_pay_months" and "lt." in params["month"]):
            raise httpx.ReadTimeout("slow")
        return [emp("A")] if table == "employees" else []
    monkeypatch.setattr(supabase, "select_all", fake)
    s = pw._read(MONTH)
    assert s["failed"] == ["ยอดสะสมจากใบ HQ", "ตารางเที่ยว แก่งคอย"] and s["trips"] is None and s["pay_tail"] is None and s["pay_prev"] == [] and "pay_old" not in s
    out = pw.build(s, MONTH, TODAY)
    assert out["summary"]["people"] == 1 and file(out, "kk_trips")["summary"] == "โหลดไม่สำเร็จ" and file(out, "hq_sheet")["summary"] == "โหลดไม่สำเร็จ"
    # the people list itself timing out: 502 with words, never a bare 500
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    monkeypatch.setattr(pw, "has_v93", lambda: True)
    monkeypatch.setattr(supabase, "select_all", lambda table, params=None, page=1000: (_ for _ in ()).throw(httpx.ConnectTimeout("down")))
    pw.forget()
    r = TestClient(main.app).get("/api/payroll/checklist?month=2026-11&fresh=true", headers={"X-App-Token": "secret"})
    assert r.status_code == 502 and "โหลดไม่สำเร็จ" in r.json()["detail"]
    pw.forget()


def test_only_a_missing_table_asks_for_the_schema_file(monkeypatch):
    from app import supabase
    answers = iter([supabase.SupabaseError("select employee_pay_rules failed: HTTP 404 {\"code\":\"PGRST205\"}"), supabase.SupabaseError("select failed: HTTP 503 upstream"), []])

    def fake(table, params=None):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(supabase, "select", fake)
    pw._probe.update(at=0.0, ok=False)
    assert pw.has_v93() is False and pw.has_v93() is False            # no such table: False, kept (the second call asks nothing)
    pw._probe.update(at=0.0, ok=False)
    with pytest.raises(supabase.SupabaseError):                       # a hiccup of the database is not "run schema_v93.sql"
        pw.has_v93()
    assert pw._probe["at"] == 0.0                                     # and is not kept
    assert pw.has_v93() is True
    pw._probe.update(at=0.0, ok=False)


def test_every_select_names_its_columns_and_nothing_private(monkeypatch):
    from app import supabase
    seen = []
    monkeypatch.setattr(supabase, "select_all", lambda table, params=None, page=1000: seen.append((table, params)) or [])
    pw._read(MONTH)
    assert {t for t, _ in seen} >= {"employees", "employee_pay_rules", "employee_rates", "employee_pay_months", "route_driver_days", "attendance_punches",
                                    "employee_loans", "employee_loan_entries"}
    private = re.compile(r"phone|bank|account_no|id_card|national|passport|password|pin\b|token|notes|end_reason|\*", re.I)
    for table, params in seen:
        assert params["select"] in pw.SELECTS.values() and "order" in params
        assert not private.search(params["select"]), (table, params["select"])
    assert pw.SELECTS["employees"] == "id,code,full_name,nickname,branch,position,status,start_date,end_date,pay_group,probation_end"


def test_the_module_only_reads():
    text = Path(pw.__file__).read_text(encoding="utf-8")
    assert not re.search(r"supabase\.(insert|update|delete|rpc|storage_\w+)\(", text)
    assert not re.search(r"\.(save|apply_month|import_report|set_review|import_batch)\(", text)
    assert "month_deductions(" not in text.split('"""', 2)[2].replace("loans.month_deductions (", "")       # its own reading: employees are never selected whole


def test_the_page_is_a_menu_of_its_own():
    m = users.MENUS["payroll"]
    assert (m["group"], m["nav"], m["hash"], m["api"]) == ("พนักงาน", ["navPayroll"], "#payroll", ["/api/payroll"])
    assert users.allowed(["payroll"], "/api/payroll/checklist")
    assert not any(users.allowed([k], "/api/payroll") for k in users.MENUS if k != "payroll")             # pay rides on no other menu
    assert not any(users.allowed(["payroll"], p) for p in ("/api/employees", "/api/loans", "/api/attendance", "/api/driver-days", "/api/payables"))
    keys = [o["key"] for o in users.menu_options() if o["group"] == "พนักงาน"]
    assert keys == ["employees", "loans", "attendance", "payroll", "documents"]


def test_the_endpoint_is_gated(monkeypatch):
    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    asked = []

    def fake(month, today, fresh=False):
        asked.append((month, fresh))
        return {"month": pw.parse_month(month) if month else "2026-11", "kinds": []}
    monkeypatch.setattr(pw, "load", fake)
    monkeypatch.setattr(users, "session_user", lambda t: {"id": 1, "name": "ก", "username": "a1", "menus": ["payroll"]} if t == "u_pay"
                        else {"id": 2, "name": "ข", "username": "a2", "menus": ["employees", "loans", "attendance", "documents", "driver_days"]} if t == "u_hr" else None)
    c = TestClient(main.app)
    assert c.get("/api/payroll/checklist?month=2026-11&fresh=true", headers={"X-App-Token": "secret"}).json()["month"] == "2026-11"
    assert asked == [("2026-11", True)]
    assert c.get("/api/payroll/checklist", headers={"X-App-Token": "u_pay"}).status_code == 200
    assert c.get("/api/payroll/checklist", headers={"X-App-Token": "u_hr"}).status_code == 403            # role_gate: HR menus do not open pay
    assert c.get("/api/payroll/checklist").status_code == 403                                            # no token
    assert c.get("/api/payroll/checklist", headers={"X-App-Token": "u_unknown"}).status_code == 403
    bad = c.get("/api/payroll/checklist?month=2026-13", headers={"X-App-Token": "secret"})
    assert bad.status_code == 400 and "เดือนไม่ถูกต้อง" in bad.json()["detail"]
    for path in ("/api/employees", "/api/loans", "/api/attendance", "/api/driver-days"):                 # the pay menu opens nothing else
        assert c.get(path, headers={"X-App-Token": "u_pay"}).status_code == 403
