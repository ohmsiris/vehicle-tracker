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
            "card_fuel": [], "cash_fuel": [], "vehicles": [], "drivers": [], "trips": [], "docs": [], "inbox": [], "advance_files": [], "penalties": [], "failed": []}
    return {**base, **kw}


def one(out, code):
    return next(p for p in out["people"] if p["code"] == code)


def kind(out, key):
    return next(k for k in out["kinds"] if k["key"] == key)


def file(out, key):
    return next(f for f in out["files"] if f["key"] == key)


@pytest.fixture
def no_hold(monkeypatch):
    """แก่งคอย counted like any branch -- what the page does again once the owner lifts the hold of 27 Nov 2026."""
    monkeypatch.setattr(pw, "HOLD_BRANCHES", ())


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
    # a truck whose fleet number is its plate: the plate once
    assert pw.truck_label({"vehicle_number": "83-2919", "plate_number": "83-2919", "vehicle_class": "รถใหญ่", "branch": "สระบุรี"}) == "83-2919 รถใหญ่ สระบุรี"


# --------------------------------------------------------------------------------------------- route drivers
def route_src(rows, **kw):
    return src(**{"employees": [emp("D1", "มานัส ศรีวิลาส", position="พนักงานขับรถสาย")], "rules": [rule("D1", "DRV-E", commission_pct=2, bonus=True)],
                  "rates": [rate("D1", 350)], "pay_prev": [prev("D1")], "route": rows, **kw})


def test_a_route_driver_with_every_day_typed():
    out = pw.build(route_src(drove("D1", START, END), docs=ADVANCE_IN), MONTH, TODAY)
    p = one(out, "D1")
    assert p["cells"]["days"] == {"state": "ok", "value": 31, "text": "31 วัน", "note": ""}
    assert p["cells"]["sales"]["state"] == "ok" and p["cells"]["sales"]["value"] == 31000 and p["cells"]["sales"]["text"] == "31,000"
    assert p["cells"]["bags"]["state"] == "na" and p["cells"]["fuel"]["state"] == "na"        # no baht per ซอง; paid on sales as typed
    assert p["state"] == "ok" and p["missing"] == [] and (p["group"], p["group_label"], p["rate"]) == ("DRV-E", "พขร. รถปลีก-เล็ก 350/วัน + 2%", 350)
    assert p["formula"] == "ค่าแรง = วัน × 350 · ค่าคอม = ยอดขาย × 2% · เบี้ยขยัน 1,000 ถ้ามาครบ 31 วัน" and p["hold"] is False
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
    assert c["days"] == {"state": "miss", "value": None, "text": "", "note": "ขับรถสายถึง 17/11/2026 · ยังไม่มีสแกนนิ้ว / ใบสรุปวันหยุด"}
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
    assert p["cells"]["sales"]["value"] == 31 * 200 and p["cells"]["sales"]["note"] == "เฉพาะยอดหลอด · ก่อนหักค่าน้ำมัน"      # the หลอด part only
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


def test_scans_are_a_stand_in_and_nothing_is_missing(no_hold):
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
    # both rates are confirmed: the rate is ok, the gap (which days are which) sits on the days cell
    assert two["cells"]["rate"] == {"state": "ok", "value": 390, "text": "390 / 344", "note": "2 อัตรา: วันออกรถอย่างเดียว 344"} and two["rate2"] == 344
    f = file(out, "scan_kk")
    assert f["state"] == "warn" and "01/11/2026" in f["summary"] and "02/11/2026" in f["summary"]          # two days of a 31-day period
    assert file(out, "scan_sb_fr")["summary"] == "ยังไม่ได้อัปโหลดงวดนี้" and file(out, "scan_sb_pd")["state"] == "bad"


def test_a_full_scan_file_is_green():
    punches = [{"employee_id": code, "punched_at": f"{(START + timedelta(days=i)).isoformat()}T08:00:00"} for i in range(31) for code in ("SB-KK001", "SB-FR001")]
    out = pw.build(src(employees=[emp("SB-KK001", branch="แก่งคอย"), emp("SB-FR001")], rules=[rule("SB-KK001"), rule("SB-FR001")], punches=punches), MONTH, TODAY)
    assert (file(out, "scan_sb_fr")["state"], file(out, "scan_sb_fr")["summary"]) == ("ok", "1 คน 31 สแกน (26/10 ถึง 25/11)")
    assert (file(out, "scan_kk")["state"], file(out, "scan_kk")["summary"]) == ("info", "แก่งคอยพักไว้ก่อน · 1 คน 31 สแกน (26/10 ถึง 25/11)")      # set aside: never a to-do


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
    # ... and แก่งคอย is set aside (owner, 27 Nov 2026): SB-KK073 stays on the list, in no count
    assert out["summary"] == {"people": 3, "ok": 0, "carry": 0, "miss": 3, "hq_paid": 1, "worked": 3, "left_before": 1, "hold": 1}
    assert [p["code"] for p in out["people"] if p["hold"]] == ["SB-KK073"] and out["hold_branches"] == ["แก่งคอย"]
    assert file(out, "start_dates")["summary"].startswith("2 คนยังไม่มีวันเริ่มงาน") and file(out, "start_dates")["state"] == "info"
    assert one(out, "SB-FR061")["status"] == {"key": "resigned", "label": "ลาออก", "detail": "วันสุดท้าย 25/11/2026"}
    assert one(out, "SB-FR061")["cells"]["refund"] == {"state": "miss", "value": 10000, "text": "10,000", "note": "สะสม 10,000 บาท ยังไม่ได้คืน"}
    assert one(out, "SB-FR200")["status"] == {"key": "new", "label": "พนักงานใหม่", "detail": "เริ่ม 10/11/2026"}
    assert one(out, "SB-KK073")["status"]["key"] == "probation" and one(out, "SB-KK073")["status"]["label"] == "ทดลองงาน"
    assert one(out, "SB-FR001")["status"] == {"key": "working", "label": "ทำงาน", "detail": ""}
    gone = one(out, "SB-FR095")
    assert gone["status"]["key"] == "left_before" and gone["status"]["label"] == "ลาออกแล้ว" and gone["status"]["detail"].startswith("รอคืนเงินสะสม")
    assert gone["formula"] == "" and gone["hold"] is False
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
    # a raise inside the period: both rates are known, each for its own dates -- ok, and the rule shows both (owner, 27 Nov 2026)
    assert one(out, "D")["cells"]["rate"] == {"state": "ok", "value": 400, "text": "380 / 400", "note": "380 ถึง 09/11/2026 · 400 ตั้งแต่ 10/11/2026"}
    assert one(out, "D")["formula"] == "ค่าแรง = วัน × 380 (ถึง 09/11/2026) + วัน × 400 (ตั้งแต่ 10/11/2026) · ไม่มีเบี้ยขยัน"
    assert one(out, "A")["formula"] == "ค่าแรง = วัน × 390 · ไม่มีเบี้ยขยัน" and one(out, "B")["formula"] == "ค่าแรง = วัน × (ยังไม่มีอัตรา) · ไม่มีเบี้ยขยัน"
    assert pw.rate_spans(rates[2:], START, END) == [(380, START, date(2026, 11, 9)), (400, date(2026, 11, 10), END)]
    late = pw.rate_cell([rate("E", 400, since="2026-11-10")], START, END, None)[0]                                    # no rate at all before the 10th
    assert (late["state"], late["text"], late["note"]) == ("carry", "– / 400", "ยังไม่มีอัตรา ถึง 09/11/2026 · 400 ตั้งแต่ 10/11/2026")
    assert any(x["title"].startswith("ยังไม่มีอัตราค่าแรง: B ") for x in out["decisions"])


def test_a_person_without_a_group():
    out = pw.build(src(employees=[emp("A")], rates=[rate("A")]), MONTH, TODAY)
    assert one(out, "A")["cells"]["group"]["state"] == "miss" and kind(out, "group")["state"] == "bad" and one(out, "A")["formula"] == ""


# --------------------------------------------------------------------------------------------- deductions
def test_last_months_sheet_stands_in_for_the_deductions():
    people = [emp("A"), emp("B"), emp("C")]
    prevs = [prev("A", {"advance": 3000, "savings": 500, "sso": 429, "common": 350, "card": 0}), prev("B", {"advance": 0, "savings": 0, "sso": 0, "common": 0})]
    out = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people], rates=[rate(p["id"]) for p in people], pay_prev=prevs), MONTH, TODAY)
    a, b, c = (one(out, x)["cells"] for x in "ABC")
    assert a["advance"] == {"state": "carry", "value": 3000, "text": "3,000", "note": "ตามใบ HQ 10/2026 · รอรายชื่อเบิกจากเจ้าของ"}
    # สะสม follows HQ's latest sheet: confirmed by the owner (27 Nov 2026), no longer a stand-in. ปกส. and เบิก still wait for him.
    assert a["savings"] == {"state": "ok", "value": 500, "text": "500", "note": "ตามใบ HQ 10/2026 (เจ้าของยืนยัน)"}
    assert a["sso"] == {"state": "carry", "value": 429, "text": "429", "note": "ตามใบ HQ 10/2026 · รอเจ้าของตรวจ"}
    assert a["common"] == {"state": "miss", "value": None, "text": "", "note": "ใบ HQ 10/2026 หัก 350 บาท · เปลี่ยนทุกเดือน ต้องใส่ใหม่"}       # never carried
    assert all(b[k]["state"] == "na" for k in ("savings", "sso", "common"))                                           # a row with zeros: not needed
    # who draws an advance changes every month: "none last month" is not "none this month" while the list is not in
    assert b["advance"] == {"state": "carry", "value": 0, "text": "0", "note": "ใบ HQ 10/2026 ไม่ได้เบิก · รอรายชื่อเบิกจากเจ้าของ"}
    assert all(c[k] == {"state": "miss", "value": None, "text": "", "note": "ไม่มีชื่อในใบ HQ 10/2026 — ยังไม่รู้ว่าต้องหักหรือไม่"} for k in ("advance", "sso"))
    # สะสม follows the sheet: someone who is not on it (a new hire) has nothing taken -- said in amber, never a red gap
    assert c["savings"] == {"state": "carry", "value": 0, "text": "0", "note": "ไม่มีชื่อในใบ HQ 10/2026: ไม่หักสะสม — ถ้าจะเริ่มหักต้องแจ้ง"}
    assert c["common"]["state"] == "na"
    k = kind(out, "advance")
    assert (k["need"], k["ok"], k["carry"], k["miss"], k["state"]) == (3, 0, 2, 1, "bad")
    assert [r["state"] for r in k["rows"]] == ["bad", "warn", "warn"] and k["rows"][0]["who"].startswith("C ")        # the worst first
    assert k["rows"][1] == {"who": "A คน ทดสอบ", "detail": "3,000 · ตามใบ HQ 10/2026 · รอรายชื่อเบิกจากเจ้าของ", "state": "warn"}      # the amount being carried shows
    assert (file(out, "prev_sheet")["state"], file(out, "prev_sheet")["summary"]) == ("ok", "ใช้ใบ 10/2026 (2 คน)") and "สะสมหักตามใบนี้" in file(out, "prev_sheet")["title"]
    sv = kind(out, "savings")                                                                  # A has it from the sheet, C is on no sheet
    assert (sv["need"], sv["ok"], sv["carry"], sv["miss"], sv["later"]) == (2, 1, 1, 0, "") and "เจ้าของยืนยัน 27/11/2026" in sv["source"]
    assert (kind(out, "sso")["carry"], kind(out, "sso")["state"]) == (1, "bad") and kind(out, "sso")["later"]
    # the two things the owner said he would bring are the last lines of the decisions, marked as waiting on him
    waiting = [d for d in out["decisions"] if d["wait"]]
    assert [d["title"] for d in waiting] == ["ปกส.: เจ้าของกำลังตรวจ", "เบิกล่วงหน้า: รอรายชื่อจากเจ้าของ"] and out["decisions"][-2:] == waiting
    assert "แทน 1 คน" in waiting[0]["detail"] and "แทน 2 คน" in waiting[1]["detail"]
    # the branch's list is booked: an advance is still only last sheet's figure (IceBox cannot read the list per person yet)
    listed = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people], rates=[rate(p["id"]) for p in people], pay_prev=prevs, docs=ADVANCE_IN), MONTH, TODAY)
    assert one(listed, "A")["cells"]["advance"]["note"] == "ตามใบ HQ 10/2026 · มีใบเบิกแล้ว แต่ระบบยังอ่านรายคนไม่ได้ · รอรายชื่อเบิกจากเจ้าของ"
    assert one(listed, "B")["cells"]["advance"]["state"] == "na"


def test_the_stand_in_is_each_persons_latest_sheet_not_only_last_months(no_hold):
    """2026-12 seen on 26/11/2026: HQ's sheets end at 2026-10 and a 2026-11 one may never come (IceBox is taking over). SB-KK061 is
    on the 09/2026 sheet only."""
    people = [emp("A"), emp("K61", branch="แก่งคอย"), emp("N")]
    sheets = [prev("A", {"advance": 3000, "savings": 500, "sso": 429, "common": 59}, parts={"ot": 0}),
              prev("K61", {"advance": 3000, "savings": 500, "sso": 0}, parts={"ot": 1080}, month="2026-09"),
              prev("A", {"advance": 0, "savings": 1000}, month="2026-08"),                                           # an older row of A: never used
              prev("A", {"advance": 9999}, month="2027-01")]                                                         # a later month than the one asked for: ignored
    out = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people], rates=[rate(p["id"]) for p in people], pay_prev=sheets), "2026-12", date(2026, 12, 20))
    a, k, n = (one(out, x)["cells"] for x in ("A", "K61", "N"))
    assert (a["advance"]["state"], a["advance"]["value"], a["savings"]["value"], a["sso"]["note"]) == ("carry", 3000, 500, "ตามใบ HQ 10/2026 · รอเจ้าของตรวจ")
    assert a["savings"]["state"] == "ok"                                                      # from the latest sheet; K61's is an older one: still a stand-in
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
    assert c["advance"]["state"] == "carry" and c["trip"]["state"] == "miss" and c["trip"]["note"] == "ยังไม่มีตารางเที่ยวงวดนี้"      # a truck helper paid by the trip (his cells are as they are, hold or not)


def test_sales_trip_money_and_ot_follow_what_was_paid_last_month(no_hold):
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


USED = "\nใช้คิดเงินของรถสาย 1 คัน (กลุ่ม A, B, C หักจากยอดขาย · ทวาย หักจากซอง)"


def test_the_sack_drivers_fuel():
    line = {"id": 1, "batch": 1, "amount": 800, "counted": True, "dup_of": None, "vehicle_id": "V27", "page_id": 7, "page": {"confirmed": True}}
    out = pw.build(fuel_src(cash_fuel=[line]), MONTH, TODAY)
    c = one(out, "D1")["cells"]["fuel"]
    assert (c["state"], c["value"], c["text"]) == ("miss", None, "")                          # a part of the fuel is never the cell's figure: it is in the note
    assert c["note"] == "รถ #27 84-2682 รถใหญ่ สระบุรี · ยังไม่มีใบแจ้งหนี้บัตรน้ำมัน · น้ำมันเงินสด 11/11-25/11 ยังไม่ได้อัปโหลด · มีแล้ว: เงินสด 800"
    assert (file(out, "card_fuel")["state"], file(out, "card_fuel")["summary"]) == ("bad", "ยังไม่มีใบแจ้งหนี้บัตรน้ำมันงวดนี้ (บางจาก / PT)" + USED)
    f = file(out, "cash_fuel")
    assert (f["state"], f["summary"]) == ("warn", "26/10-10/11 ยืนยันแล้ว 800 บาท · 11/11-25/11 ยังไม่ได้อัปโหลด" + USED)        # a half by its dates, never "งวด 26-10"
    card = [{"id": "e1", "source_id": "fuel:bangchak:2026-11:842682", "lines": [{"vehicle_id": "V27", "credit": 0}, {"vehicle_id": "V27", "credit": 29740}]},
            {"id": "e2", "source_id": "fuel:pt:2026-11:xx", "lines": [{"vehicle_id": "V9", "credit": 1000}]}]
    out = pw.build(fuel_src(cash_fuel=[line, {**line, "id": 2, "batch": 2, "amount": 200}], card_fuel=card), MONTH, TODAY)
    c = one(out, "D1")["cells"]["fuel"]
    assert c == {"state": "ok", "value": 30740, "text": "30,740", "note": "รถ #27 84-2682 รถใหญ่ สระบุรี · บัตร 29,740 + เงินสด 1,000"}
    assert (file(out, "card_fuel")["state"], file(out, "card_fuel")["summary"]) == ("ok", "บางจาก 1 คัน 29,740 บาท · PT 1 คัน 1,000 บาท" + USED)
    assert file(out, "cash_fuel")["state"] == "ok"
    assert kind(out, "fuel")["need"] == 1 and kind(out, "fuel")["source"].endswith("กลุ่ม A, B, C และทวาย (1 คัน)")
    assert one(out, "D1")["cells"]["sales"]["note"] == ""                                     # ทวาย's fuel comes off his ซอง, never off his sales
    assert one(out, "D1")["formula"] == "ค่าแรง = วัน × 350 · ค่าคอม = ยอดขายหลอด × 2% · ค่าซอง = (จำนวนซอง − ค่าน้ำมัน ÷ 140) × 3 บาท · เบี้ยขยัน 1,000 ถ้ามาครบ 31 วัน"


def test_groups_a_b_c_are_paid_on_sales_after_the_trucks_fuel():
    """Owner, 27 Nov 2026: the workbook's rule stands -- commission on sales AFTER the truck's fuel. What is typed in ยอดขายรายวัน is
    before fuel, so each of those trucks' fuel (card + cash) is an input, exactly like ทวาย's."""
    line = {"id": 1, "batch": 1, "amount": 800, "counted": True, "dup_of": None, "vehicle_id": "V27", "page_id": 7, "page": {"confirmed": True}}
    card = [{"id": "e1", "source_id": "fuel:bangchak:2026-11:842682", "lines": [{"vehicle_id": "V27", "credit": 29740}]},
            {"id": "e2", "source_id": "fuel:pt:2026-11:xx", "lines": [{"vehicle_id": "V9", "credit": 1000}]}]

    def a(**kw):
        s = fuel_src(**kw)
        s["rules"][0].update(pay_group="DRV-A", commission_pct=5, bag_rate=None)
        s["rates"] = [rate("D1", 120)]
        return s
    # nothing in: missing, and the sales are named as the figure before fuel
    out = pw.build(a(), MONTH, TODAY)
    c = one(out, "D1")["cells"]
    assert c["fuel"] == {"state": "miss", "value": None, "text": "", "note": "รถ #27 84-2682 รถใหญ่ สระบุรี · ยังไม่มีใบแจ้งหนี้บัตรน้ำมัน · "
                         "น้ำมันเงินสด 26/10-10/11 ยังไม่ได้อัปโหลด · น้ำมันเงินสด 11/11-25/11 ยังไม่ได้อัปโหลด"}
    assert (c["sales"]["state"], c["sales"]["value"], c["sales"]["note"]) == ("ok", 31000, "ก่อนหักค่าน้ำมัน") and one(out, "D1")["missing"] == ["ค่าน้ำมัน"]
    assert one(out, "D1")["formula"] == "ค่าแรง = วัน × 120 · ค่าคอม = (ยอดขาย − ค่าน้ำมันรถ) × 5% · เบี้ยขยัน 1,000 ถ้ามาครบ 31 วัน"
    # the card statements are in, cash fuel is not: still missing -- the card part is what is known
    c = one(pw.build(a(card_fuel=card), MONTH, TODAY), "D1")["cells"]["fuel"]
    assert (c["state"], c["value"], c["text"]) == ("miss", None, "") and c["note"].endswith("น้ำมันเงินสด 11/11-25/11 ยังไม่ได้อัปโหลด · มีแล้ว: บัตร 29,740")
    assert "ใบแจ้งหนี้" not in c["note"]
    # one card station only: a truck may fill at the other one
    c = one(pw.build(a(card_fuel=card[1:], cash_fuel=[line, {**line, "id": 2, "batch": 2}]), MONTH, TODAY), "D1")["cells"]["fuel"]
    assert (c["state"], c["value"]) == ("miss", None) and "ยังไม่มีใบแจ้งหนี้บัตรน้ำมัน บางจาก ·" in c["note"] and c["note"].endswith("มีแล้ว: เงินสด 1,600")
    # a cash page still to check
    wait = {**line, "id": 3, "batch": 2, "page": {"confirmed": False}}
    c = one(pw.build(a(card_fuel=card, cash_fuel=[line, wait]), MONTH, TODAY), "D1")["cells"]["fuel"]
    assert c["state"] == "miss" and "น้ำมันเงินสด 11/11-25/11 อัปโหลดแล้ว รอตรวจ" in c["note"] and c["value"] is None and c["note"].endswith("มีแล้ว: บัตร 29,740 + เงินสด 800")
    # all of it in: ok, and the sales say what is left after it
    out = pw.build(a(card_fuel=card, cash_fuel=[line, {**line, "id": 2, "batch": 2, "amount": 200}]), MONTH, TODAY)
    c = one(out, "D1")["cells"]
    assert (c["fuel"]["state"], c["fuel"]["value"]) == ("ok", 30740) and c["sales"]["note"] == "ก่อนหักค่าน้ำมัน · หลังหัก 260" and c["sales"]["value"] == 31000
    assert one(out, "D1")["missing"] == [] and kind(out, "fuel")["state"] == "ok"
    # all of it in and nothing for this truck: 0 is a real answer
    other = [{**e, "lines": [{"vehicle_id": "V9", "credit": 500}]} for e in card]
    c = one(pw.build(a(card_fuel=other, cash_fuel=[{**line, "vehicle_id": "V9"}, {**line, "id": 2, "batch": 2, "vehicle_id": "V9"}]), MONTH, TODAY), "D1")["cells"]
    assert (c["fuel"]["state"], c["fuel"]["value"], c["fuel"]["text"]) == ("ok", 0, "0") and c["sales"]["note"] == "ก่อนหักค่าน้ำมัน · หลังหัก 31,000"
    # the truck is found by the driver's name within his own branch (both branches have a #6); no truck: missing, and said
    s = a(card_fuel=card)
    s["vehicles"][0]["branch"] = "แก่งคอย"
    assert one(pw.build(s, MONTH, TODAY), "D1")["cells"]["fuel"]["note"].startswith("ไม่พบรถของคนขับ")
    # a source that could not be read
    c = one(pw.build(a(card_fuel=None, cash_fuel=None, failed=["บัตรน้ำมัน"]), MONTH, TODAY), "D1")["cells"]["fuel"]
    assert c["state"] == "miss" and "โหลดข้อมูลบัตรน้ำมันไม่สำเร็จ" in c["note"] and "โหลดน้ำมันเงินสดไม่สำเร็จ" in c["note"]
    # B and C: the same, on the หลอด part for a driver who types ซอง and หลอด apart
    s = a(card_fuel=card, cash_fuel=[line, {**line, "id": 2, "batch": 2, "amount": 200}], split=[{"employee_id": "D1", "money_split": True}])
    s["route"] = drove("D1", START, END, money=1500, money_parts={"zong": 300, "lod": 1200})
    s["rules"][0].update(pay_group="DRV-B", bag_rate=10)
    p = one(pw.build(s, MONTH, TODAY), "D1")
    assert p["cells"]["sales"]["note"] == "เฉพาะยอดหลอด · ก่อนหักค่าน้ำมัน · หลังหัก 6,460" and p["cells"]["fuel"]["state"] == "ok"
    assert p["formula"] == "ค่าแรง = วัน × 120 · ค่าคอม = (ยอดขายหลอด − ค่าน้ำมันรถ) × 5% · ค่าซอง = จำนวนซอง × 10 บาท · เบี้ยขยัน 1,000 ถ้ามาครบ 31 วัน"
    s["rules"][0].update(pay_group="DRV-C", commission_pct=3.5, bag_rate=3)
    assert one(pw.build(s, MONTH, TODAY), "D1")["formula"].startswith("ค่าแรง = วัน × 120 · ค่าคอม = (ยอดขายหลอด − ค่าน้ำมันรถ) × 3.5% · ค่าซอง = จำนวนซอง × 3 บาท")
    # days still untyped: the sales are short, so nothing is said about "after fuel" yet
    s["route"] = s["route"][:-2]
    assert one(pw.build(s, MONTH, TODAY), "D1")["cells"]["sales"]["note"] == "เฉพาะยอดหลอด · ก่อนหักค่าน้ำมัน · ยังขาด 2 วัน"


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
    # แก่งคอย is set aside: its papers are named when they are in, and their absence is no gap
    assert (file(out, "advance_list")["state"], file(out, "advance_list")["summary"]) == ("ok", "สระบุรี 2 หน้า · แก่งคอยพักไว้ก่อน")
    assert (file(out, "month_papers")["state"], file(out, "month_papers")["summary"]) == ("warn", "แก่งคอย 1 หน้า · ยังไม่มี: สระบุรี")
    assert (file(out, "hq_sheet")["state"], file(out, "hq_sheet")["summary"]) == ("ok", "มีแล้ว 1 คน")
    empty = pw.build(src(employees=[emp("A")], rules=[rule("A")]), MONTH, TODAY)
    assert (file(empty, "month_papers")["state"], file(empty, "month_papers")["summary"]) == ("bad", "ยังไม่ได้อัปโหลด")
    assert (file(empty, "hq_sheet")["state"], file(empty, "hq_sheet")["summary"]) == ("info", "ยังไม่มี (ใช้เทียบผลเท่านั้น)")
    assert file(empty, "kk_trips")["state"] == "info" and file(empty, "kk_trips")["link"] is None
    assert file(empty, "kk_trips")["summary"] == "แก่งคอยพักไว้ก่อน · ยังไม่มีตารางเที่ยวงวดนี้ — ยังอัปโหลดเองไม่ได้ ต้องแจ้งให้ลงให้"
    assert (file(empty, "scan_kk")["state"], file(empty, "scan_kk")["summary"]) == ("info", "แก่งคอยพักไว้ก่อน · ยังไม่ได้อัปโหลดงวดนี้")
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
    s = src(employees=people, rules=[rule("A"), rule("B"), rule("C", "DRV-E")], rates=[rate("A"), rate("B"), rate("C")],
            pay_prev=[prev("A", {"advance": 3000}), prev("B"), prev("C")], route=drove("C", START, END), docs=ADVANCE_IN,
            punches=[{"employee_id": "A", "punched_at": "2026-11-01T08:00:00"}])
    out = pw.build(s, MONTH, TODAY)
    assert [one(out, x)["state"] for x in "ABC"] == ["carry", "miss", "ok"]                   # miss beats carry beats ok
    assert out["summary"] == {"people": 3, "ok": 1, "carry": 1, "miss": 1, "hq_paid": 0, "worked": 3, "left_before": 0, "hold": 0}
    states = {k["key"]: k["state"] for k in out["kinds"]}
    assert states["group"] == "ok" and states["days"] == "bad" and states["advance"] == "warn" and states["fuel"] == "na" and states["sales"] == "ok"
    assert [k["key"] for k in out["kinds"]] == pw.KEYS == [c["key"] for c in out["columns"]]
    assert all({"key", "title", "state", "need", "ok", "carry", "miss", "source", "rows", "more", "link", "link_label", "note", "later"} == set(k) for k in out["kinds"])
    assert len(out["decisions"]) <= 10 and all(set(d) == {"title", "detail", "wait"} for d in out["decisions"])
    assert set(out) >= {"rules", "hold_branches", "summary", "files", "kinds", "columns", "people", "decisions", "failed"}


def test_the_kinds_follow_the_workbooks_columns_and_never_read_as_an_all_clear():
    # sheet คำนวณ: F วันทำงาน G ยอดขาย H ซอง I น้ำมัน J OT K ค่าเที่ยว L งดเบี้ยขยัน ... X สะสม Y เบิก Z เงินกู้ AA ส่วนกลาง AC เงินขาด AD อื่นๆ AE ปกส. AH คืนสะสม
    assert pw.KEYS == ["group", "rate", "days", "sales", "bags", "fuel", "ot", "trip", "nobonus", "savings", "advance", "loan", "common", "penalty", "other", "sso", "refund"]
    out = pw.build(src(employees=[emp("A")], rules=[rule("A")], rates=[rate("A")]), MONTH, TODAY)
    pen = kind(out, "penalty")                                                                # nobody has a letter, but the papers that would carry one are not in
    assert (pen["need"], pen["state"]) == (0, "warn") and "ยังไม่ได้อัปโหลดเอกสารสิ้นเดือน" in pen["note"] and "เงินขาดของคนขับ" in pen["later"]
    assert kind(out, "nobonus")["state"] == "warn" and kind(out, "other")["state"] == "warn" and kind(out, "common")["state"] == "warn"
    assert kind(out, "fuel")["state"] == "na" and kind(out, "fuel")["note"] == ""              # truly nobody: fuel is asked of the A, B, C, D route drivers only
    assert kind(out, "fuel")["source"] == "ใบแจ้งหนี้บัตรน้ำมัน + น้ำมันเงินสด ของรถสาย กลุ่ม A, B, C และทวาย"
    assert kind(out, "sso")["later"] and not kind(out, "loan")["later"] and not kind(out, "sales")["later"]
    assert not kind(out, "savings")["later"]                                                  # confirmed: it follows HQ's latest sheet
    # เบิกล่วงหน้า is read per person from the mid-month list in the drop box: an upload can turn it green, so it is no "later" any more
    assert not kind(out, "advance")["later"] and "กล่องรับไฟล์" in kind(out, "advance")["source"] and kind(out, "advance")["link"] == "#accounts-in"
    papers = [{"batch": f"docs:2026-11:{b}:end", "feed": "endmonth", "branch": b, "kind": "expense", "page": 1} for b in pw.BRANCHES]
    full = pw.build(src(employees=[emp("A")], rules=[rule("A")], rates=[rate("A")], docs=papers + ADVANCE_IN), MONTH, TODAY)
    assert (kind(full, "penalty")["state"], kind(full, "penalty")["note"]) == ("na", "") and "ยังไม่มีที่ลง" in kind(full, "common")["note"]
    # the papers of a branch that is set aside are not waited for
    sb = pw.build(src(employees=[emp("A")], rules=[rule("A")], rates=[rate("A")], docs=[papers[0], ADVANCE_IN[0]]), MONTH, TODAY)
    assert (kind(sb, "penalty")["state"], kind(sb, "penalty")["note"]) == ("na", "") and "ยังไม่มีที่ลง" in kind(sb, "common")["note"]


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
    s = route_src(None, failed=["ยอดขายรายวัน", "บัตรน้ำมัน"], card_fuel=None, punches=None, docs=None, loans=None, pay_prev=None, trips=None, uploads=None)
    out = pw.build(s, MONTH, TODAY)
    c = one(out, "D1")["cells"]
    assert c["days"]["state"] == "miss" and c["days"]["note"] == "โหลดยอดขายรายวันไม่สำเร็จ" and c["advance"]["note"] == "โหลดใบสรุปเงินเดือน HQ ไม่สำเร็จ"
    assert out["failed"] == ["ยอดขายรายวัน", "บัตรน้ำมัน"]
    assert all(file(out, k)["summary"] == "โหลดไม่สำเร็จ" for k in ("route_sales", "scan_kk", "advance_list", "card_fuel", "loans_mark", "kk_trips", "prev_sheet"))


def test_the_decisions_come_from_the_data():
    """Settled by the owner on 27 Nov 2026 and no longer asked: who gets เบี้ยขยัน, แก่งคอย's OT, sales before or after fuel. What is
    still open stays, built from the data."""
    people = [emp("A"), emp("K", branch="แก่งคอย"), emp("D", position="พนักงานขับรถสาย"), emp("T"), emp("K2", branch="แก่งคอย")]
    rules = [rule("A"), rule("K", "KK-PROD", ot_auto_hours=3, ot_rate=40), rule("D", "DRV-A", bonus=True, commission_pct=5), rule("T", rate2=344),
             rule("K2", rate2=344)]
    out = pw.build(src(employees=people, rules=rules, rates=[rate(p["id"]) for p in people], route=drove("D", START, END)), MONTH, TODAY)
    titles = [d["title"] for d in out["decisions"]]
    assert titles == ["พนักงานผลิตที่ออกรถด้วย: ต้องรู้จำนวนวันออกรถอย่างเดียว", "สแกนนิ้วครั้งเดียวในวัน: นับเป็น 1 วันเต็มหรือไม่"]
    assert not any(w in " ".join(titles) for w in ("เบี้ยขยัน", "OT", "น้ำมัน")) and not any(d["wait"] for d in out["decisions"])
    assert "T คน ทดสอบ (390 / 344)" in out["decisions"][0]["detail"] and "K2" not in out["decisions"][0]["detail"]      # nothing is asked about a branch set aside
    # nobody without a rate: that line is gone; someone without one brings it back, first
    s = src(employees=people + [emp("N")], rules=rules + [rule("N")], rates=[rate(p["id"]) for p in people])
    assert [d["title"] for d in pw.build(s, MONTH, TODAY)["decisions"]][0] == "ยังไม่มีอัตราค่าแรง: N คน ทดสอบ"


def test_a_route_that_ended_inside_the_period_with_a_rate_from_that_date():
    """SB-PD011 (owner, 27 Nov 2026): 350 on his route to 17/11/2026, 400 at หน้าโรง from 18/11/2026. Two rates inside one period, and
    his fingerprint scans stand in for the days after the route."""
    s = route_src(drove("D1", START, date(2026, 11, 17)))
    s["rules"][0].update(route_until="2026-11-17")
    s["rates"] = [rate("D1", 400, since="2026-11-18"), rate("D1", 350)]
    out = pw.build(s, MONTH, TODAY)
    p = one(out, "D1")
    assert p["cells"]["rate"] == {"state": "ok", "value": 400, "text": "350 / 400", "note": "350 ถึง 17/11/2026 · 400 ตั้งแต่ 18/11/2026"}
    assert p["formula"] == "ค่าแรง = วัน × 350 (ถึง 17/11/2026) + วัน × 400 (ตั้งแต่ 18/11/2026) · ค่าคอม = ยอดขาย × 2% · เบี้ยขยัน 1,000 ถ้ามาครบ 31 วัน"
    assert (p["cells"]["days"]["state"], p["cells"]["days"]["note"]) == ("miss", "18/11-25/11 ทำงานอื่น ยังไม่มีข้อมูลวันทำงาน")      # no scans: nothing to go on
    assert not any("ระหว่างงวด" in d["title"] for d in out["decisions"])                      # the rate after the route is there: settled
    assert next(g for g in out["rules"]["groups"] if g["group"] == "DRV-E")["formula"].startswith("ค่าแรง = วัน × 350 ·")      # the group's rule keeps the route's rate
    # his scans after the route: a stand-in (amber), counted with the route days
    s["punches"] = [{"employee_id": "D1", "punched_at": f"2026-11-{d}T07:00:00"} for d in (16, 17, 18, 19, 20, 22, 25)]
    c = one(pw.build(s, MONTH, TODAY), "D1")["cells"]["days"]
    assert c == {"state": "carry", "value": 28, "text": "28 วัน", "note": "ขับรถสาย 23 วัน · 18/11-25/11 ทำงานอื่น: มีสแกน 5 วัน ยังไม่ได้สรุป"}
    # route days still untyped beat the stand-in
    s["route"] = s["route"][:-2]
    c = one(pw.build(s, MONTH, TODAY), "D1")["cells"]["days"]
    assert c["state"] == "miss" and c["note"].startswith("ยังไม่ได้ลง 16/11, 17/11 · ขับรถสาย 21 วัน · 18/11-25/11 ทำงานอื่น: มีสแกน 5 วัน")
    # the same route end with no rate starting after it: the question stays
    s["rates"] = [rate("D1", 350)]
    out = pw.build(s, MONTH, TODAY)
    assert any(d["title"].startswith("รถสายที่หยุดวิ่งระหว่างงวด") and "17/11/2026" in d["detail"] for d in out["decisions"])
    assert one(out, "D1")["cells"]["rate"]["text"] == "350"


# --------------------------------------------------------------------------------------------- the rules card
def test_the_rules_are_shown_as_the_workbook_writes_them():
    """Owner, 27 Nov 2026: the workbook's written rules are the primary rules for now, and must be visible."""
    people = [emp("A1", position="พนักงานขับรถสาย"), emp("A2", position="พนักงานขับรถสาย"), emp("B1", position="พนักงานขับรถสาย"),
              emp("E1", position="พนักงานขับรถสาย"), emp("E2", position="พนักงานขับรถสาย"), emp("F1"), emp("F2"), emp("P1"), emp("W1"), emp("W2"),
              emp("KD", branch="แก่งคอย"), emp("KP", branch="แก่งคอย"), emp("KW", branch="แก่งคอย"), emp("GONE", status="resigned", end_date="2026-09-30")]
    rules = [rule("A1", "DRV-A", commission_pct=5, bonus=True), rule("A2", "DRV-A", commission_pct=5, bonus=True),
             rule("B1", "DRV-B", commission_pct=5, bag_rate=10, bonus=True), rule("E1", "DRV-E", commission_pct=2, bonus=True),
             rule("E2", "DRV-E", commission_pct=2.5, bonus=True), rule("F1", "DRV-F", bonus=True), rule("F2", "DRV-F", bonus=True), rule("P1", "FAC-COM", ot_rate=40),
             rule("W1", ot_rate=40), rule("W2", rate2=344), rule("KD", "KK-DRV", bonus=True), rule("KP", "KK-PROD", ot_rate=40, ot_auto_hours=3), rule("KW"),
             rule("GONE", "DRV-C", commission_pct=9, bag_rate=9, bonus=True)]
    rates = [rate("A1", 120), rate("A2", 120), rate("B1", 120), rate("E1", 350), rate("E2", 350), rate("F1", 400), rate("F2", 430), rate("P1", 300), rate("W1"), rate("W2"),
             rate("KD", 400), rate("KP", 325), rate("KW", 325)]
    out = pw.build(src(employees=people, rules=rules, rates=rates), MONTH, TODAY)
    r = out["rules"]
    assert r["source"] == "แบบคำนวณเงินเดือน (เสนอ) · เจ้าของยืนยัน 27/11/2026" and set(r) == {"general", "groups", "source"}
    g = r["general"]
    assert len(g) == 10 and all(isinstance(x, str) and x for x in g)
    assert g[0] == "งวดเงินเดือน: วันที่ 26 ของเดือนก่อน ถึง 25 ของเดือนนี้ (งวดนี้ 26/10/2026 ถึง 25/11/2026 · 31 วัน)"
    assert g[1] == "รวมรับ = ค่าแรง + ค่าคอม + ค่าซอง + เบี้ยขยัน + OT"
    assert g[2] == "เบี้ยขยัน 1,000 บาท: เฉพาะพนักงานขับรถ เมื่อมาทำงานครบ 31 วันของงวด และไม่ถูกงด"
    assert "กลุ่ม A, B, C" in g[3] and "หักค่าน้ำมันรถออกจากยอดขายก่อนคิดค่าคอม" in g[3] and "จำนวนซอง − ค่าน้ำมัน ÷ 140" in g[3]
    assert g[4].startswith("พนักงานผลิตที่ออกรถด้วย (2 อัตรา): ") and "วันออกรถอย่างเดียวจ่ายอัตราที่สอง" in g[4] and "OT สระบุรี" in g[5] and "ไม่รวมในเงินเดือน" in g[5]
    assert g[6] == "วันที่มาสแกนแต่ไม่ได้ทำงาน ไม่จ่าย" and "สะสม หักตามใบสรุปเงินเดือน HQ ล่าสุด" == g[7] and "ไม่ใช่รายได้ของเดือน" in g[8]
    assert g[9].startswith("แก่งคอยพักไว้ก่อน")
    assert [x["group"] for x in r["groups"]] == ["DRV-A", "DRV-B", "DRV-C", "DRV-D", "DRV-E", "DRV-F", "FAC-COM", "DAY", "KK-DRV", "KK-PROD"]
    by = {x["group"]: x for x in r["groups"]}
    assert all(set(x) - {"hold"} == {"group", "label", "formula", "people", "bonus"} for x in r["groups"])
    # the numbers of the people in the group, where they all have the same one
    assert by["DRV-A"] == {"group": "DRV-A", "label": "พขร. รถหลอดใหญ่ 120/วัน + 5%", "people": 2, "bonus": True,
                           "formula": "ค่าแรง = วัน × 120 · ค่าคอม = (ยอดขาย − ค่าน้ำมันรถ) × 5% · เบี้ยขยัน 1,000"}
    assert by["DRV-B"]["formula"] == "ค่าแรง = วัน × 120 · ค่าคอม = (ยอดขายหลอด − ค่าน้ำมันรถ) × 5% · ค่าซอง = จำนวนซอง × 10 บาท · เบี้ยขยัน 1,000"
    # nobody of this period is in C or D (the one who left earlier does not count): the workbook's own numbers
    assert (by["DRV-C"]["people"], by["DRV-C"]["bonus"]) == (0, True)
    assert by["DRV-C"]["formula"] == "ค่าแรง = วัน × 203 · ค่าคอม = (ยอดขายหลอด − ค่าน้ำมันรถ) × 3.5% · ค่าซอง = จำนวนซอง × 3 บาท · เบี้ยขยัน 1,000"
    assert by["DRV-D"]["formula"] == "ค่าแรง = วัน × 368 · ค่าคอม = ยอดขายหลอด × 2% · ค่าซอง = (จำนวนซอง − ค่าน้ำมัน ÷ 140) × 3 บาท · เบี้ยขยัน 1,000"
    assert by["DRV-E"]["formula"] == "ค่าแรง = วัน × 350 · ค่าคอม = ยอดขาย × % ของแต่ละคน · เบี้ยขยัน 1,000"                # two different %
    assert by["DRV-F"]["formula"] == "ค่าแรง = วัน × อัตราของแต่ละคน · ไม่มีค่าคอม · เบี้ยขยัน 1,000"                          # 400 and 430
    assert (by["FAC-COM"]["formula"], by["FAC-COM"]["bonus"]) == ("ค่าแรง = วัน × 300 · (+ % ของยอดขาย ถ้ามี)", False)
    assert (by["DAY"]["formula"], by["DAY"]["bonus"], by["DAY"]["people"], by["DAY"]["hold"]) == ("ค่าแรง = วัน × อัตราของแต่ละคน", False, 3, 1)
    assert by["KK-DRV"] == {"group": "KK-DRV", "label": "พขร. แก่งคอย (ค่าเที่ยว)", "people": 1, "bonus": True, "hold": 1,
                            "formula": "ค่าแรง = วัน × 400 · ค่าเที่ยว = กรอกเอง · เบี้ยขยัน 1,000"}
    assert (by["KK-PROD"]["formula"], by["KK-PROD"]["bonus"], by["KK-PROD"]["hold"]) == ("ค่าแรง = วัน × 325 · OT = ชม. OT × 40 บาท", False, 1)
    assert "hold" not in by["DRV-A"] and "hold" not in by["FAC-COM"]
    # เบี้ยขยัน: drivers only (DRV-A..F and KK-DRV)
    assert {x["group"] for x in r["groups"] if x["bonus"]} == set(pw.DRIVER_GROUPS)
    # each person: the same rule with his own numbers
    f = {p["code"]: p["formula"] for p in out["people"]}
    assert f["E2"] == "ค่าแรง = วัน × 350 · ค่าคอม = ยอดขาย × 2.5% · เบี้ยขยัน 1,000 ถ้ามาครบ 31 วัน"
    assert f["F2"] == "ค่าแรง = วัน × 430 · ไม่มีค่าคอม · เบี้ยขยัน 1,000 ถ้ามาครบ 31 วัน"
    assert f["P1"] == "ค่าแรง = วัน × 300 · ไม่มีเบี้ยขยัน"                                    # แพ็ค with no %: nothing is promised
    assert f["W2"] == "ค่าแรง = วันทำงานในโรงงาน × 390 + วันออกรถอย่างเดียว × 344 · ไม่มีเบี้ยขยัน"
    assert f["KP"] == "ค่าแรง = วัน × 325 · OT = ชม. OT × 40 บาท · ไม่มีเบี้ยขยัน" and f["KD"].endswith("ค่าเที่ยว = กรอกเอง · เบี้ยขยัน 1,000 ถ้ามาครบ 31 วัน")
    assert pw.formula_words("FAC-COM", "วัน × 300", {"pct": 1.5}, "ไม่มีเบี้ยขยัน", person=True) == "ค่าแรง = วัน × 300 · ค่าคอม = ยอดขาย × 1.5% · ไม่มีเบี้ยขยัน"
    assert pw.formula_words("DRV-A", "วัน × 120", {}, person=True) == "ค่าแรง = วัน × 120 · ค่าคอม = (ยอดขาย − ค่าน้ำมันรถ) × (ยังไม่มี %)"
    assert (pw.num(5.0), pw.num(3.5), pw.num(1000), pw.num(2.25)) == ("5", "3.5", "1,000", "2.25")


# --------------------------------------------------------------------------------------------- a branch that is set aside
def kk_src():
    people = [emp("S1"), emp("S2"), emp("K1", branch="แก่งคอย"), emp("K2", branch="แก่งคอย"), emp("K4", "หนึ่ง ทดสอบ", branch="แก่งคอย", position="พนักงานขับรถสาย"),
              emp("K9", branch="แก่งคอย", status="resigned", end_date="2026-10-20")]
    rules = [rule("S1"), rule("S2"), rule("K1", "KK-PROD", ot_rate=40, ot_auto_hours=3), rule("K2", "KK-DRV", bonus=True), rule("K4", "KK-DRV", bonus=True)]
    tail = [{"month": "2026-10", "code": "K9", "employee_id": "K9", "refund": 0, "savings_balance": 1500}]
    return src(employees=people, rules=rules, rates=[rate(p["id"]) for p in people], pay_tail=tail,
               pay_prev=[prev("S1", {"savings": 500, "sso": 429}), prev("S2"), prev("K1", {"savings": 500}, parts={"ot": 900}), prev("K2", parts={"commission": 5000}),
                         prev("K4", parts={"commission": 3143}, sales=157130)],
               punches=[{"employee_id": "S1", "punched_at": f"{(START + timedelta(days=i)).isoformat()}T08:00:00"} for i in range(31)], docs=ADVANCE_IN[:1])


def test_a_branch_on_hold_is_listed_but_counted_nowhere():
    """Owner, 27 Nov 2026: แก่งคอย is on hold (he will fill its OT in himself later). It must not count as missing or drown สระบุรี."""
    assert pw.HOLD_BRANCHES == ("แก่งคอย",)
    out = pw.build(kk_src(), MONTH, TODAY)
    # everyone is still listed, แก่งคอย's people with "hold" and their cells as they are; a leaver waiting for savings is not "on hold"
    assert [(p["code"], p["hold"]) for p in out["people"]] == [("S1", False), ("S2", False), ("K1", True), ("K2", True), ("K4", True), ("K9", False)]
    k1 = one(out, "K1")["cells"]
    assert k1["ot"]["state"] == "miss" and k1["days"]["state"] == "miss" and k1["savings"]["state"] == "ok" and one(out, "K1")["state"] == "miss"
    assert one(out, "K2")["cells"]["trip"]["state"] == "miss" and one(out, "K4")["cells"]["sales"]["state"] == "miss"
    # the headline is about the others
    assert out["summary"] == {"people": 2, "ok": 0, "carry": 1, "miss": 1, "hq_paid": 0, "worked": 2, "left_before": 1, "hold": 3}
    assert out["hold_branches"] == ["แก่งคอย"]
    # every kind counts only the others, and says how many are set aside
    days, ot, trip, sales, refund = (kind(out, k) for k in ("days", "ot", "trip", "sales", "refund"))
    assert (days["need"], days["ok"], days["carry"], days["miss"], days["hold"], len(days["rows"])) == (2, 0, 1, 1, 3, 2)
    assert (ot["need"], ot["rows"], ot["state"], ot["hold"]) == (0, [], "na", 1) and (trip["need"], trip["state"], trip["hold"]) == (0, "na", 1)
    assert (sales["need"], sales["hold"]) == (0, 1) and (refund["need"], refund["miss"]) == (1, 1) and "hold" not in refund
    assert not any("K1" in r["who"] or "K2" in r["who"] for k in out["kinds"] for r in k["rows"])
    assert all(set(k) - {"hold"} == {"key", "title", "state", "need", "ok", "carry", "miss", "source", "rows", "more", "link", "link_label", "note", "later"} for k in out["kinds"])
    # the files that are only about that branch are information; a driver of that branch is not chased for his sales
    assert (file(out, "scan_kk")["state"], file(out, "scan_kk")["summary"]) == ("info", "แก่งคอยพักไว้ก่อน · ยังไม่ได้อัปโหลดงวดนี้")
    assert file(out, "kk_trips")["state"] == "info" and file(out, "kk_trips")["summary"].startswith("แก่งคอยพักไว้ก่อน · ยังไม่มีตารางเที่ยวงวดนี้")
    assert (file(out, "route_sales")["state"], file(out, "route_sales")["summary"]) == ("ok", "ลงครบ 0 คน ถึง 25/11/2026")
    assert (file(out, "advance_list")["state"], file(out, "advance_list")["summary"]) == ("ok", "สระบุรี 1 หน้า · แก่งคอยพักไว้ก่อน")
    assert file(out, "scan_sb_fr")["state"] == "bad" and file(out, "start_dates")["summary"].startswith("2 คน")
    # nothing is asked about its people (K4's % of sales, the automatic OT)
    assert not any("K4" in d["title"] or "แก่งคอย" in d["title"] or "OT" in d["title"] for d in out["decisions"])
    assert out["rules"]["general"][-1] == "แก่งคอยพักไว้ก่อน: เจ้าของจะใส่ OT เองภายหลัง ยังไม่นับในตัวเลขของหน้านี้ (คืนเงินสะสมของคนที่ลาออกยังนับตามปกติ)"
    # the savings of its leaver are money to pay back: counted, and the line says whose are among them
    assert refund["source"].endswith("· รวมคนที่ลาออกของแก่งคอย 1 คน (เงินที่ต้องคืน ไม่ได้พักไว้)") and "แก่งคอย" not in kind(out, "savings")["source"]


def test_without_the_hold_the_branch_counts_again(no_hold):
    out = pw.build(kk_src(), MONTH, TODAY)
    assert out["summary"] == {"people": 5, "ok": 0, "carry": 1, "miss": 4, "hq_paid": 0, "worked": 5, "left_before": 1, "hold": 0}
    assert out["hold_branches"] == [] and not any(p["hold"] for p in out["people"]) and not any("hold" in k for k in out["kinds"])
    assert (file(out, "scan_kk")["state"], file(out, "scan_kk")["summary"]) == ("bad", "ยังไม่ได้อัปโหลดงวดนี้") and file(out, "kk_trips")["state"] == "bad"
    assert (file(out, "advance_list")["state"], file(out, "advance_list")["summary"]) == ("warn", "สระบุรี 1 หน้า · ยังไม่มี: แก่งคอย")
    assert file(out, "route_sales")["state"] == "bad" and "K4 หนึ่ง ทดสอบ" in file(out, "route_sales")["summary"]
    assert any(d["title"].startswith("ค่าคอมจากยอดขาย: K4 ") for d in out["decisions"]) and (kind(out, "ot")["need"], kind(out, "trip")["need"]) == (1, 1)
    assert len(out["rules"]["general"]) == 9 and "hold" not in next(g for g in out["rules"]["groups"] if g["group"] == "KK-DRV")


# --------------------------------------------------------------------------------------------- codes the staff list does not have
def test_a_scan_code_that_is_not_in_the_staff_list():
    """27 Nov 2026: the สระบุรี factory file carries SB-FR112, who is not an employee -- his scans are counted for nobody."""
    punches = [{"employee_id": code, "punched_at": f"{(START + timedelta(days=i)).isoformat()}T08:00:00"} for i in range(31) for code in ("SB-FR001", "SB-PD001")]
    ups = [{"id": "u0", "filename": "1_report (9).xls", "period_start": "2026-09-26", "period_end": "2026-10-25", "unknown_codes": "SB-FR999", "created_at": "2026-10-27T03:47:45"},
           {"id": "u1", "filename": "1_report (12).xls", "period_start": "2026-10-26", "period_end": "2026-11-25", "unknown_codes": None, "created_at": "2026-11-27T03:54:34"},
           {"id": "u2", "filename": "1_report (11).xls", "period_start": "2026-10-26", "period_end": "2026-11-25", "unknown_codes": "SB-FR112, SB-FR001, sb-fr113  นาย ก",
            "created_at": "2026-11-27T05:21:39"}]
    s = src(employees=[emp("SB-FR001"), emp("SB-PD001")], rules=[rule("SB-FR001"), rule("SB-PD001")], punches=punches, uploads=ups)
    out = pw.build(s, MONTH, TODAY)
    f = file(out, "scan_sb_fr")
    assert (f["state"], f["link"], f["link_label"]) == ("warn", "#employees", "รายชื่อพนักงาน")
    assert f["summary"] == "1 คน 31 สแกน (26/10 ถึง 25/11)\nมีรหัสที่ไม่อยู่ในรายชื่อพนักงาน: SB-FR112, sb-fr113 นาย ก — เพิ่มพนักงานก่อน ไม่งั้นไม่ถูกนับ"
    assert (file(out, "scan_sb_pd")["state"], file(out, "scan_sb_pd")["link"]) == ("ok", "#attendance")              # the other file is clean
    # last month's upload is not this period's; a code that is an employee by now is no longer unknown; the same file read again replaces its line
    assert pw.unknown_codes(ups, s["employees"], START, END) == ["SB-FR112", "sb-fr113 นาย ก"]
    again = ups + [{**ups[2], "id": "u3", "unknown_codes": None, "created_at": "2026-11-27T06:00:00"}]
    assert pw.unknown_codes(again, s["employees"], START, END) == [] and pw.unknown_codes(None, [], START, END) == []
    s["uploads"] = again
    assert file(pw.build(s, MONTH, TODAY), "scan_sb_fr")["state"] == "ok"
    many = [{**ups[2], "unknown_codes": ", ".join(f"SB-FR2{i:02d}" for i in range(8))}]
    assert "SB-FR205 และอีก 2 รหัส —" in file(pw.build({**s, "uploads": many}, MONTH, TODAY), "scan_sb_fr")["summary"]


# --------------------------------------------------------------------------------------------- after the review of 27 Nov 2026
CASH = [{"id": 1, "batch": 1, "amount": 800, "counted": True, "dup_of": None, "vehicle_id": "V27", "page_id": 7, "page": {"confirmed": True}},
        {"id": 2, "batch": 2, "amount": 200, "counted": True, "dup_of": None, "vehicle_id": "V27", "page_id": 8, "page": {"confirmed": True}}]
PT = {"id": "e2", "source_id": "fuel:pt:2026-11:xx", "lines": [{"vehicle_id": "V9", "credit": 1000}]}


def test_bangchak_needs_both_of_its_station_files():
    """The drop box books บางจาก one station file at a time (NM, ห้วยบง): with only one of them in, a truck that fills at the other
    would read as a real 0 -- so fuel is not complete, and the absent station is named."""
    from app import inbox_r_fuel
    assert pw.BANGCHAK_STATIONS == inbox_r_fuel.BC_STATIONS
    nm = {"id": "e1", "source_id": "fuel:bangchak:2026-11:NM:842682", "lines": [{"vehicle_id": "V27", "credit": 12000}]}
    hb = {"id": "e3", "source_id": "fuel:bangchak:2026-11:ห้วยบง:842682", "lines": [{"vehicle_id": "V27", "credit": 5000}]}
    out = pw.build(fuel_src(cash_fuel=CASH, card_fuel=[nm, PT]), MONTH, TODAY)
    c = one(out, "D1")["cells"]["fuel"]
    assert (c["state"], c["value"], c["text"]) == ("miss", None, "")
    assert c["note"] == "รถ #27 84-2682 รถใหญ่ สระบุรี · ยังไม่มีใบแจ้งหนี้บัตรน้ำมัน บางจาก ห้วยบง · มีแล้ว: บัตร 12,000 + เงินสด 1,000"
    f = file(out, "card_fuel")
    assert (f["state"], f["summary"]) == ("warn", "บางจาก 1 คัน 12,000 บาท · PT 1 คัน 1,000 บาท · ยังไม่มี: บางจาก ห้วยบง" + USED)
    assert (kind(out, "fuel")["miss"], kind(out, "fuel")["ok"]) == (1, 0)
    # both station files in: complete -- and a truck that fills at both is one truck
    out = pw.build(fuel_src(cash_fuel=CASH, card_fuel=[nm, hb, PT]), MONTH, TODAY)
    assert one(out, "D1")["cells"]["fuel"] == {"state": "ok", "value": 18000, "text": "18,000", "note": "รถ #27 84-2682 รถใหญ่ สระบุรี · บัตร 17,000 + เงินสด 1,000"}
    assert (file(out, "card_fuel")["state"], file(out, "card_fuel")["summary"]) == ("ok", "บางจาก 1 คัน 17,000 บาท · PT 1 คัน 1,000 บาท" + USED)
    assert pw.card_fuel_view([hb, PT])["lack"] == ["บางจาก NM"] and pw.card_fuel_view([nm, hb])["lack"] == ["PT"]
    assert pw.card_fuel_view([])["lack"] == ["บางจาก", "PT"] and pw.card_fuel_view(None)["stations"] == {}
    # one of the older entries (both stations in one, no station in its key) is complete by itself
    old = pw.card_fuel_view([{"source_id": "fuel:bangchak:2026-10:842682", "lines": [{"vehicle_id": "V27", "credit": 29740}]}, PT])
    assert (old["lack"], old["estimated"]) == ([], [])
    # an estimate is a figure to go on, never a statement: a stand-in, and no "after fuel" is promised on it
    est = [{"id": "e4", "source_id": "fuel:bangchak:2026-11:est-842682", "lines": [{"vehicle_id": "V27", "credit": 9000}]}, {**PT, "source_id": "fuel:pt:2026-11:est-xx"}]
    s = fuel_src(cash_fuel=CASH, card_fuel=est)
    s["rules"][0].update(pay_group="DRV-A", commission_pct=5, bag_rate=None)
    out = pw.build(s, MONTH, TODAY)
    c = one(out, "D1")["cells"]
    assert (c["fuel"]["state"], c["fuel"]["value"]) == ("carry", 10000) and c["fuel"]["note"].endswith("· บัตร บางจาก, PT เป็นยอดประมาณ ยังไม่ใช่ใบแจ้งหนี้")
    assert c["sales"]["note"] == "ก่อนหักค่าน้ำมัน" and file(out, "card_fuel")["state"] == "warn" and "· ยอดประมาณ: บางจาก, PT" in file(out, "card_fuel")["summary"]


def test_the_loans_to_do_leaves_out_a_branch_on_hold():
    """The to-do line counts the same people as the loan kind: แก่งคอย's loans are named beside it, never added to it."""
    people = [emp("A"), emp("K", branch="แก่งคอย"), emp("K2", branch="แก่งคอย")]
    loans = [loan("A"), loan("K", repay_amount=500), loan("K2", repay_mode="flexible", repay_amount=None, amount=5000)]
    entries = [paid("A", "2026-10"), paid("K", "2026-10", 500), paid("K2", "2026-10", 2000)]
    out = pw.build(src(employees=people, rules=[rule(p["id"]) for p in people], loans=loans, loan_entries=entries), MONTH, TODAY)
    f, k = file(out, "loans_mark"), kind(out, "loan")
    assert (f["state"], f["summary"]) == ("warn", "ยังไม่ได้กด 1 รายการ 1,500 บาท · แก่งคอยพักไว้ก่อน 2 รายการ 500 บาท")
    assert (k["need"], k["carry"], k["miss"], k["hold"]) == (1, 1, 0, 2)


def test_the_groups_rule_keeps_its_numbers_when_someone_is_left_in_it_after_his_route():
    """SB-PD011 seen in 2026-12: still in DRV-E, at หน้าโรง on 400 since 18/11/2026. The rule of the 21 others must go on saying 350."""
    s = route_src(drove("D2", date(2026, 11, 26), date(2026, 12, 19)))
    s["rules"][0].update(route_until="2026-11-17", commission_pct=9)
    s["rates"] = [rate("D1", 400, since="2026-11-18"), rate("D1", 350), rate("D2", 350)]
    s["employees"].append(emp("D2", "วิชัย ทดสอบ", position="พนักงานขับรถสาย"))
    s["rules"].append(rule("D2", "DRV-E", commission_pct=2, bonus=True))
    out = pw.build(s, "2026-12", date(2026, 12, 20))
    g = next(x for x in out["rules"]["groups"] if x["group"] == "DRV-E")
    assert (g["people"], g["formula"]) == (2, "ค่าแรง = วัน × 350 · ค่าคอม = ยอดขาย × 2% · เบี้ยขยัน 1,000")
    assert one(out, "D1")["cells"]["days"]["note"].startswith("ขับรถสายถึง 17/11/2026 · ")          # the last day he drove, as his rate note says
    # alone in the group, the rule shows the workbook's own numbers
    s["employees"], s["rules"] = s["employees"][:1], s["rules"][:1]
    g = next(x for x in pw.build(s, "2026-12", date(2026, 12, 20))["rules"]["groups"] if x["group"] == "DRV-E")
    assert (g["people"], g["formula"]) == (1, "ค่าแรง = วัน × 350 · ค่าคอม = ยอดขาย × 2% · เบี้ยขยัน 1,000")


def test_spare_days_and_the_trucks_fuel_are_a_question_for_the_owner():
    """The truck's fuel is of the whole period, the driver's sales of his own days only: whose fuel a สำรองขับ day is has not been settled."""
    card = [{"id": "e1", "source_id": "fuel:bangchak:2026-11:842682", "lines": [{"vehicle_id": "V27", "credit": 2000}]}, PT]
    s = fuel_src(cash_fuel=CASH, card_fuel=card)
    s["rules"][0].update(pay_group="DRV-A", commission_pct=5, bag_rate=None)
    assert not any(d["title"].startswith("วันสำรองขับ") for d in pw.build(s, MONTH, TODAY)["decisions"])            # nobody had a spare day: not asked
    for r in s["route"][5:7]:
        r.update(spare=True)
    out = pw.build(s, MONTH, TODAY)
    c = one(out, "D1")["cells"]
    assert c["fuel"]["state"] == "ok" and c["fuel"]["note"].endswith("บัตร 2,000 + เงินสด 1,000 · รวมค่าน้ำมันของวันสำรองขับ 2 วัน")
    assert c["sales"]["note"] == "ก่อนหักค่าน้ำมัน · หลังหัก 26,000 (รวมค่าน้ำมันของวันสำรองขับ 2 วัน)"
    d = next(x for x in out["decisions"] if x["title"] == "วันสำรองขับ: ค่าน้ำมันรถของวันนั้นหักจากใคร")
    assert d["detail"].startswith("D1 มานัส ศรีวิลาส 2 วัน — ") and d["wait"] is False


def test_the_tube_and_sack_driver_on_3_5_percent_is_asked_about():
    """The workbook writes "ยอดขาย × 3.5%" for DRV-C; the page works on the หลอด part (his ซอง are paid apart). Shown as it is used, and asked."""
    rows = drove("D1", START, END, money=500, money_parts={"zong": 300, "lod": 200})
    s = route_src(rows, split=[{"employee_id": "D1", "money_split": True}])
    s["rules"][0].update(pay_group="DRV-C", commission_pct=3.5, bag_rate=3)
    out = pw.build(s, MONTH, TODAY)
    assert "ค่าคอม = (ยอดขายหลอด − ค่าน้ำมันรถ) × 3.5%" in one(out, "D1")["formula"] and "เฉพาะยอดหลอด" in one(out, "D1")["cells"]["sales"]["note"]
    d = next(x for x in out["decisions"] if x["title"] == "D1 มานัส ศรีวิลาส: ค่าคอม 3.5% คิดจากยอดหลอดอย่างเดียว หรือรวมยอดซอง")
    assert '"ยอดขาย × 3.5%"' in d["detail"] and "3 บาท/ซอง" in d["detail"]
    s["rules"][0].update(pay_group="DRV-B", commission_pct=5, bag_rate=10)                     # B's rule is written on หลอด already: nothing to ask
    assert not any("คิดจากยอดหลอดอย่างเดียว" in x["title"] for x in pw.build(s, MONTH, TODAY)["decisions"])


def test_ot_that_is_asked_of_a_daily_paid_person_is_in_his_rule(no_hold):
    """แก่งคอย's รายวัน people were paid OT on HQ's sheet: the hours are an input, so the rule names them (the workbook's "OT ถ้ามี")."""
    people = [emp("K5", branch="แก่งคอย"), emp("K7", branch="แก่งคอย"), emp("S1")]
    out = pw.build(src(employees=people, rules=[rule("K5", ot_rate=40), rule("K7"), rule("S1")], rates=[rate(p["id"]) for p in people],
                       pay_prev=[prev("K5", parts={"ot": 480}), prev("K7", parts={"ot": 80}), prev("S1", parts={"ot": 120})]), MONTH, TODAY)
    assert one(out, "K5")["formula"] == "ค่าแรง = วัน × 390 · OT = ชม. OT × 40 บาท · ไม่มีเบี้ยขยัน"
    assert one(out, "K7")["formula"] == "ค่าแรง = วัน × 390 · OT = ชม. OT × (ยังไม่มีอัตรา OT) บาท · ไม่มีเบี้ยขยัน"
    assert one(out, "S1")["formula"] == "ค่าแรง = วัน × 390 · ไม่มีเบี้ยขยัน"                  # สระบุรี: OT is cash, out of pay
    assert next(g for g in out["rules"]["groups"] if g["group"] == "DAY")["formula"] == "ค่าแรง = วัน × 390 · (แก่งคอย: + OT ถ้ามี)"


# --------------------------------------------------------------------------------------------- เบิกล่วงหน้า from the mid-month lists in the drop box
DRIVERS = "รายการเบิกกลางเดือน : พนักงานขับรถ เดือน ตุลาคม 2569"                     # HQ forgot to change the month: the real file of 11/2026 says ตุลาคม
FACTORY = "รายการเบิกกลางเดือน : ช่าง / ออฟฟิศ / ฝ่ายผลิต เดือน พฤศจิกายน 2569"
SRB = "[SRB] รายการเบิกกลางเดือน15 พฤศจิกายน69"                                     # the file as a note names it
SAID = f"ตามใบเบิกกลางเดือน {SRB}"


def drew(text, amount=3000, unsure=False, account="1500"):
    """A line as the drop box's reader gives it."""
    return {"date": None, "text": text, "plate": None, "amount": amount, "unsure": unsure, "account": account, "truck_number": None}


def page(n, lines, title=FACTORY, total=None, kind_="advance", codes=(), summary=""):
    return {"n": n, "page_kind": kind_, "title": title, "written_total": total, "summary": summary, "employee_codes": list(codes), "lines": lines,
            "date_from": None, "date_to": None, "branch_hints": [], "person": ""}


def box(pages, fid="f1", branch="สระบุรี", status="read", name=None, at="2026-11-26T08:30:21+00:00", **kw):
    """A row of inbox_files as the page selects it (the read pages under "pages")."""
    return {"id": fid, "file_name": name or (f"{SRB}.pdf" if branch == "สระบุรี" else "[KK] รายการเบิกกลางเดือน15 พฤศจิกายน69.pdf"), "kind": "scan", "status": status,
            "month": MONTH, "branches": [branch], "uploaded_at": at, "booked_at": None, "half": "mid", "pages": pages, **kw}


def test_the_staff_code_in_a_list_line():
    assert pw.read_code("นายทวาย กำจันทร์ SB-PD001") == ("SB-PD001", "นายทวาย กำจันทร์", "")
    assert pw.read_code("SB-KK001 นายสายัณต์ พวนกิ่ง") == ("SB-KK001", "นายสายัณต์ พวนกิ่ง", "")
    assert pw.read_code("sb - kk 073S นาย ทศพร สุขประเสริฐ") == ("SB-KK073", "นาย ทศพร สุขประเสริฐ", "")          # spaces, lower case, a stray letter
    assert pw.read_code("ส้ม (MR. WIN KO KO) SB FR103") == ("SB-FR103", "ส้ม (MR. WIN KO KO)", "")
    assert pw.read_code("คืน ปกส ช่างอาต (SB-FR002) ตค.69") == ("SB-FR002", "คืน ปกส ช่างอาต ตค.69", "")
    assert pw.read_code("นาย ก SB-FR7") == ("SB-FR007", "นาย ก", "")
    assert pw.read_code("นายรวม ขอนบกกลาง") == (None, "นายรวม ขอนบกกลาง", "")                                   # no code
    # a code the reader got wrong (the real 11/2026 file: SB-PO022 for SB-PD022): no staff code, and what was written is kept
    assert pw.read_code("นายประสงค์ หลักทรัพย์ SB-PO022") == (None, "นายประสงค์ หลักทรัพย์", "SB-PO022")
    assert pw.name_key("นายประสงค์  หลักทรัพย์") == pw.name_key("ประสงค์ หลักทรัพย์") == "ประสงค์ หลักทรัพย์"
    assert pw.name_key("นางสาว เค้ก(MRS.KHIN KHIN AYE)") == "เค้ก" and pw.name_key("ตุ๊กตา ( Mrs. THU ZAR WIN )") == "ตุ๊กตา"
    assert pw.list_name(DRIVERS) == "พนักงานขับรถ" and pw.list_name(FACTORY) == "ช่าง / ออฟฟิศ / ฝ่ายผลิต"
    assert pw.list_name("บันทึกการทำงาน+เบิกกลางเดือน บจก.สระบุรีวารีเทพ (แก่งคอย) ประจำเดือน 15 พฤศจิกายน 2569") == "บันทึกการทำงาน+เบิกกลางเดือน บจก.สระบุรีวารีเทพ (แก่งคอย)"
    assert pw.list_name("รายการเบิกกลางเดือน พนักงานขับรถ") == "รายการเบิกกลางเดือน พนักงานขับรถ" and pw.short_name(f"{SRB}.pdf") == SRB
    # the page's own list of codes fills a line without one: only when it has one staff code per line, in order
    lines = [drew("นาย ก SB-FR001"), drew("นาย ข"), drew("นาย ค SB-FR003")]
    got = pw.page_lines(page(1, lines, codes=["SB-FR001", "SB-FR002", "SB-FR003"]))
    assert [(r["code"], r["name"], r["via"]) for r in got] == [("SB-FR001", "นาย ก", "text"), ("SB-FR002", "นาย ข", "page"), ("SB-FR003", "นาย ค", "text")]
    assert [r["code"] for r in pw.page_lines(page(1, lines, codes=["SB-FR001", "SB-FR002", "SB-FR003", "SB-FR004"]))] == ["SB-FR001", None, "SB-FR003"]    # more codes than lines
    assert [r["code"] for r in pw.page_lines(page(1, lines, codes=["SB-FR001", "SB-FR002", "SB-FR009"]))] == ["SB-FR001", None, "SB-FR003"]                # the order does not agree
    # only account 1500 with an amount; a typed total among the lines is not a person (a driver called รวม is)
    mixed = [drew("นายรวม ขอนบกกลาง SB-PD018"), drew("ค่าโทรศัพท์", 1924, account="7200"), drew("นาย ง SB-PD019", None), drew("นาย จ SB-PD020", 0), drew("รวม", 3000)]
    assert [(r["code"], r["amount"]) for r in pw.page_lines(page(1, mixed))] == [("SB-PD018", 3000)]


def test_a_list_is_complete_by_a_running_total_or_by_page_totals():
    a, b, c = drew("นาย ก SB-PD001"), drew("นาย ข SB-PD002"), drew("นาย ค SB-PD003")

    def one_list(pages, **kw):
        got = pw.advance_lists([box(pages, **kw)])
        assert len(got) == 1
        return got[0]
    # a grand total at the foot of the last page, nothing on the first (the real drivers' list: None, then 111,000)
    x = one_list([page(7, [a, b], DRIVERS), page(8, [c], DRIVERS, total=9000)])
    assert (x["name"], x["pages"], len(x["lines"]), x["total"], x["written"], x["complete"], x["why"]) == ("พนักงานขับรถ", [7, 8], 3, 9000, 9000, True, "")
    assert (x["branch"], x["file"], x["short"], x["status"], x["cover"], x["cover_why"]) == ("สระบุรี", f"{SRB}.pdf", SRB, "read", None, "")
    assert x["lines"][2] == {"code": "SB-PD003", "raw": "", "name": "นาย ค", "amount": 3000, "unsure": False, "via": "text", "file": f"{SRB}.pdf", "short": SRB,
                             "branch": "สระบุรี", "page": 8, "title": DRIVERS, "list": "พนักงานขับรถ"}
    # a total per page
    x = one_list([page(9, [a, b], total=6000), page(10, [c], total=3000)])
    assert (x["total"], x["written"], x["complete"]) == (9000, 9000, True)
    # both at once (the real factory list: 84,000 and 57,000 per page, then 144,000 -- the grand total -- on the last)
    x = one_list([page(9, [a], total=3000), page(10, [b], total=3000), page(11, [c], total=9000, summary="หน้าสุดท้าย ยอดหน้า 3,000 รวมทั้งหมด 9,000")])
    assert (x["total"], x["written"], x["complete"]) == (9000, 9000, True)
    # a line the reader lost shows under either way of writing totals
    x = one_list([page(7, [a], DRIVERS), page(8, [c], DRIVERS, total=9000)])
    assert (x["total"], x["written"], x["complete"], x["why"]) == (6000, 9000, False, "อ่านได้ 6,000 เขียนไว้ 9,000")
    x = one_list([page(9, [a], total=6000), page(10, [c], total=3000)])
    assert (x["complete"], x["why"]) == (False, "อ่านได้ 6,000 เขียนไว้ 3,000")
    # no total written on any page: it cannot be confirmed -- a total in the reader's own summary may be its sum of what it read
    for said in ("รายการเบิกกลางเดือน (ลำดับ 33-37) รวม 9000", "3 คน คนละ 3000 รวม 9,000 บาท", "32 คน คนละ 3000 บาท"):
        x = one_list([page(7, [a, b], DRIVERS), page(8, [c], DRIVERS, summary=said)])
        assert (x["complete"], x["written"], x["why"]) == (False, None, "อ่านได้ 9,000 แต่ไม่มียอดรวมเขียนไว้ให้เทียบ")
    # an amount or a total that came as text with a comma is the number it is
    x = one_list([page(7, [drew("นาย ก SB-PD001", "3,000"), drew("นาย ข SB-PD002", " 3 000 ")], DRIVERS, total="6,000")])
    assert (len(x["lines"]), x["total"], x["written"], x["complete"]) == (2, 6000, 6000, True)
    # ... and a line whose amount cannot be read is said when the list does not add up (never when it does: the total proves the rest)
    x = one_list([page(7, [a, drew("นาย ข SB-PD002", "3,OOO"), drew("นาย ค SB-PD003", None)], DRIVERS, total=9000)])
    assert (x["total"], x["complete"], x["why"]) == (3000, False, "อ่านได้ 3,000 เขียนไว้ 9,000 · มี 2 บรรทัดที่ไม่มียอดหรืออ่านยอดไม่ได้")
    x = one_list([page(7, [a, b, c, drew("นาย ง SB-PD004", None)], DRIVERS, total=9000)])
    assert (x["complete"], x["why"]) == (True, "")
    # a page of the file that failed to read may be a page of the list
    x = one_list([page(7, [a, b], DRIVERS), page(8, [c], DRIVERS, total=9000), {"n": 9, "page_kind": "error", "error": "x", "lines": []}])
    assert (x["complete"], x["why"]) == (False, "อ่านไม่สำเร็จ หน้า 9")
    # the table's own shape (preview.ai_pages) reads the same
    x = one_list(None, half=None, preview={"half": "mid", "ai_pages": [page(7, [a, b, c], DRIVERS, total=9000)]})
    assert (x["total"], x["complete"]) == (9000, True)


def test_the_lists_of_a_file_and_the_files_that_count():
    a, b, c = drew("นาย ก SB-PD001"), drew("นาย ข SB-FR002"), drew("นาย ค SB-FR003", 2000)
    cover = page(1, [], "รายการสรุปค่าใช้จ่าย สระบุรี", total=486647, kind_="cover")
    petty = page(2, [drew("ค่าโทรศัพท์โรงงาน", 1924, account="7200"), drew("ปะยาง 1 วง", 400, account="6110")], "รายการเบิก สาขาสระบุรี", total=2324, kind_="expense")
    fuel = page(5, [drew("84-4607", 1500, account="6130")], "รายการเบิก สาขาสระบุรี", total=1500, kind_="fuel")
    pages = [cover, petty, fuel, page(7, [a], DRIVERS, total=3000), page(9, [b], FACTORY), page(10, [c], FACTORY.replace("ฝ่ายผลิต", "ฝ่าย ผลิต"), total=5000)]
    got = pw.advance_lists([box(pages)])
    # petty cash and fuel are other accounts: only the two lists, told apart by their heading (a heading spelt two ways is one list)
    assert [(x["name"], x["pages"], x["total"], x["complete"]) for x in got] == [("พนักงานขับรถ", [7], 3000, True), ("ช่าง / ออฟฟิศ / ฝ่ายผลิต", [9, 10], 5000, True)]
    assert got[0]["named"] == ["SB-PD001"] and got[1]["named"] == ["SB-FR002", "SB-FR003"]
    # the end-of-month file carries the signed copies of the same lists; a file kept unread, failed or waiting for a choice has nothing to give
    assert pw.advance_lists([box(pages, half="end")]) == [] and pw.advance_lists(None) == []
    assert all(pw.advance_lists([box(pages, status=s)]) == [] for s in ("stored", "failed", "needs_ai", "ai_running", "needs_choice"))
    # the same list uploaded twice is taken once: from the booked file, else the newest
    twice = pw.advance_lists([box(pages, fid="old", at="2026-11-20T01:00:00+00:00"), box(pages, fid="new", at="2026-11-26T01:00:00+00:00")])
    assert [(x["file_id"], x["name"]) for x in twice] == [("new", "พนักงานขับรถ"), ("new", "ช่าง / ออฟฟิศ / ฝ่ายผลิต")] and all(x["complete"] for x in twice)
    twice = pw.advance_lists([box(pages, fid="old", status="booked", at="2026-11-20T01:00:00+00:00"), box(pages, fid="new", at="2026-11-26T01:00:00+00:00")])
    assert {x["file_id"] for x in twice} == {"old"}
    # ... and when the two do not say the same, the list cannot be confirmed
    other = [page(7, [a, drew("นาย ง SB-PD004")], DRIVERS, total=6000)]
    twice = pw.advance_lists([box(pages, fid="old", at="2026-11-20T01:00:00+00:00"), box(other, fid="new", at="2026-11-26T01:00:00+00:00")])
    drivers = next(x for x in twice if x["name"] == "พนักงานขับรถ")
    assert (drivers["file_id"], drivers["complete"]) == ("new", False) and "2 ไฟล์" in drivers["why"]
    # an advance line (account 1500) on a petty-cash, fuel or cover page is not the mid-month list: nobody's cell, no list, no total
    spent = page(2, [drew("เบิกล่วงหน้า นาย ข SB-FR002", 500), drew("ค่าน้ำ", 90, account="7200")], "รายการเบิก สาขาสระบุรี", total=590, kind_="expense")
    cover = page(1, [drew("เบิกกลางเดือน", 6000)], "รายการสรุปค่าใช้จ่าย สระบุรี", total=6590, kind_="cover")
    assert pw.advance_lists([box([cover, spent])]) == []
    view = pw.advance_view([box([cover, spent])], [emp("SB-PD001"), emp("SB-FR002")])
    assert view["branches"] == {} and view["by_person"] == {} and view["lists"] == []
    view = pw.advance_view([box([cover, spent, page(7, [a], DRIVERS, total=3000)])], [emp("SB-PD001"), emp("SB-FR002")])
    assert list(view["by_person"]) == ["SB-PD001"] and [(x["name"], x["total"]) for x in view["lists"]] == [("พนักงานขับรถ", 3000)]
    got = view["branches"]["สระบุรี"]
    assert (got["people"], got["total"], got["complete"], got["why"]) == (1, 3000, True, [])
    # a file with no branch written on it: the codes say whose list it is
    assert pw.advance_lists([{**box([page(6, [drew("SB-KK001 นาย ก")], total=3000)]), "branches": None}])[0]["branch"] == "แก่งคอย"


def advance_src(pages, people=None, prevs=None, **kw):
    people = people or [emp(f"SB-FR{i:03d}") for i in range(1, 10)]
    prevs = prevs if prevs is not None else [prev("SB-FR001", {"advance": 3000}), prev("SB-FR002", {"advance": 3000}), prev("SB-FR003", {"advance": 3000}),
                                             prev("SB-FR004", {"advance": 3000}), prev("SB-FR005", {"advance": 3000}), prev("SB-FR006"), prev("SB-FR007")]
    return src(**{"employees": people, "rules": [rule(p["id"]) for p in people], "rates": [rate(p["id"]) for p in people], "pay_prev": prevs,
                  "advance_files": [box(pages)] if pages is not None else [], **kw})


def factory_pages(last_total=19000, unsure=True):
    """One list on two pages: 001 and 006 at 3,000, 002 at 5,000, 004 on a line the reader was unsure of, 005 at 2,000, 008 (on no
    HQ sheet) at 3,000; 007 is printed with no amount, 003 and 009 are not on it."""
    return [page(9, [drew("นาย ก SB-FR001"), drew("นาย ข SB-FR002", 5000), drew("นาย ง SB-FR004", unsure=unsure), drew("นาย จ SB-FR005", 2000), drew("นาย ฉ SB-FR006")], total=16000,
                 codes=["SB-FR001", "SB-FR002", "SB-FR004", "SB-FR005", "SB-FR006", "SB-FR007"]),
            page(10, [drew("นาย ซ SB-FR008")], total=last_total, codes=["SB-FR008"])]


def test_the_advance_comes_from_the_months_list():
    out = pw.build(advance_src(factory_pages()), MONTH, TODAY)
    c = {p["code"][-1]: p["cells"]["advance"] for p in out["people"]}
    assert c["1"] == {"state": "ok", "value": 3000, "text": "3,000", "note": SAID}                                    # on the list, the same as last month
    assert c["2"] == {"state": "ok", "value": 5000, "text": "5,000", "note": f"{SAID} · เดือนก่อน 3,000"}                               # another amount than last month
    assert c["3"] == {"state": "ok", "value": 0, "text": "0", "note": "ไม่มีชื่อในใบเบิกกลางเดือน — ไม่ได้เบิก · เดือนก่อน 3,000"}         # not on a complete list
    assert c["4"] == {"state": "carry", "value": 3000, "text": "3,000", "note": f"{SAID} · อ่านไม่ชัด ต้องตรวจกับใบเบิก"}                # a line marked unsure
    assert c["5"] == {"state": "ok", "value": 2000, "text": "2,000", "note": f"{SAID} · เดือนก่อน 3,000"}
    assert c["6"] == {"state": "ok", "value": 3000, "text": "3,000", "note": f"{SAID} · เดือนก่อนไม่ได้เบิก"}
    assert c["7"] == {"state": "ok", "value": 0, "text": "0", "note": "มีชื่อในใบเบิกกลางเดือน แต่ไม่มียอดเบิก — ไม่ได้เบิก"}            # printed on the list, no amount
    assert c["8"] == {"state": "ok", "value": 3000, "text": "3,000", "note": SAID}                                    # on no HQ sheet: the list is enough
    assert c["9"] == {"state": "ok", "value": 0, "text": "0", "note": "ไม่มีชื่อในใบเบิกกลางเดือน — ไม่ได้เบิก"}
    k = kind(out, "advance")
    assert (k["need"], k["ok"], k["carry"], k["miss"], k["state"], k["later"]) == (9, 8, 1, 0, "warn", "")
    assert k["rows"] == [{"who": "SB-FR004 คน ทดสอบ", "detail": f"3,000 · {SAID} · อ่านไม่ชัด ต้องตรวจกับใบเบิก", "state": "warn"}]
    assert k["source"] == "ใบเบิกกลางเดือนที่อ่านจากกล่องรับไฟล์ — สระบุรี: ช่าง / ออฟฟิศ / ฝ่ายผลิต 6 คน 19,000"
    assert out["advance_unmatched"] == [] and "มีใบเบิกแล้ว" in kind(out, "common")["note"]
    # what is asked of the owner changes with it: no list to wait for, one unclear line to check against the paper
    waiting = [d for d in out["decisions"] if d["wait"]]
    assert [d["title"] for d in waiting] == ["เบิกล่วงหน้า: รอเจ้าของตรวจกับใบเบิก"] and "ยืนยันไม่ได้ 1 คน: SB-FR004 คน ทดสอบ" in waiting[0]["detail"]
    # ... and nothing at all once every counted person's advance is the month's own
    clear = pw.build(advance_src(factory_pages(unsure=False)), MONTH, TODAY)
    assert (kind(clear, "advance")["ok"], kind(clear, "advance")["state"]) == (9, "ok") and not any("เบิกล่วงหน้า" in d["title"] for d in clear["decisions"])
    # a month HQ already worked out keeps HQ's own figure
    done = advance_src(factory_pages(), prevs=[prev("SB-FR003", {"advance": 1500}, month=MONTH)])
    assert one(pw.build(done, MONTH, TODAY), "SB-FR003")["cells"]["advance"]["note"] == "ตามใบ HQ 11/2026 (HQ คิดงวดนี้แล้ว)"


def test_a_list_that_cannot_be_confirmed_keeps_the_stand_in():
    why = "(ช่าง / ออฟฟิศ / ฝ่ายผลิต: อ่านได้ 19,000 เขียนไว้ 22,000)"
    doubt = f"ไม่มียอดเบิกในใบเบิกกลางเดือน แต่ยังยืนยันไม่ได้ว่าใบเบิกครบ {why}"
    out = pw.build(advance_src(factory_pages(last_total=22000)), MONTH, TODAY)             # the list says 22,000, 19,000 was read: a line is missing
    c = {p["code"][-1]: p["cells"]["advance"] for p in out["people"]}
    assert c["1"] == {"state": "ok", "value": 3000, "text": "3,000", "note": SAID}                                    # his own line was read
    assert c["3"] == {"state": "carry", "value": 3000, "text": "3,000", "note": f"ตามใบ HQ 10/2026 · {doubt}"}        # may be the missing line: last month's stands in
    assert c["7"] == {"state": "carry", "value": 0, "text": "0", "note": f"ใบ HQ 10/2026 ไม่ได้เบิก · {doubt}"}
    assert c["9"] == {"state": "miss", "value": None, "text": "", "note": f"ไม่มีชื่อในใบ HQ 10/2026 — ยังไม่รู้ว่าต้องหักหรือไม่ · {doubt}"}
    f = file(out, "advance_list")
    assert (f["state"], f["link"]) == ("warn", "#accounts-in")
    assert f["summary"] == f"สระบุรี อ่านแล้ว 6 คน 19,000 บาท · ยังไม่ได้กดลงบัญชีในกล่องรับไฟล์ · ยังยืนยันไม่ได้ว่าครบ {why} · แก่งคอยพักไว้ก่อน"
    assert kind(out, "advance")["source"].endswith("ช่าง / ออฟฟิศ / ฝ่ายผลิต 6 คน 19,000 (ยังยืนยันยอดไม่ได้)")
    d = next(d for d in out["decisions"] if d["title"] == "เบิกล่วงหน้า: รอเจ้าของตรวจกับใบเบิก")
    assert d["wait"] and "ยืนยันไม่ได้ 3 คน" in d["detail"]                                                           # 003, 004 (unsure), 007 -- 009 is a red gap, not a stand-in
    # a page of the file that failed to read: the same
    broken = pw.build(advance_src(factory_pages() + [{"n": 11, "page_kind": "error", "error": "x", "lines": []}]), MONTH, TODAY)
    assert one(broken, "SB-FR003")["cells"]["advance"]["note"].endswith("(ช่าง / ออฟฟิศ / ฝ่ายผลิต: อ่านไม่สำเร็จ หน้า 11)")
    # the drop box itself could not be read: the stand-in, and the reason
    failed = pw.build(advance_src(None, advance_files=None, failed=["ใบเบิกกลางเดือน"]), MONTH, TODAY)
    assert one(failed, "SB-FR003")["cells"]["advance"] == {"state": "carry", "value": 3000, "text": "3,000", "note": "ตามใบ HQ 10/2026 · โหลดใบเบิกกลางเดือนไม่สำเร็จ"}
    assert (file(failed, "advance_list")["state"], file(failed, "advance_list")["summary"]) == ("bad", "โหลดไม่สำเร็จ")


def test_a_code_on_two_lines_is_checked_not_summed_in_green():
    # HQ's list has one line a person: the second SB-FR002 is far more likely แหยม (SB-FR003) with his code misread. The list's total
    # still adds up, so only the repeat itself can say it: 002 is a stand-in, and 003 is not told "you drew nothing"
    people = [emp("SB-FR001"), emp("SB-FR002", "สอาด เถื่อนผา"), emp("SB-FR003", "แหยม"), emp("SB-FR004")]
    pages = [page(9, [drew("นาย ก SB-FR001"), drew("นายสอาด เถื่อนผา SB-FR002"), drew("นายแหยม SB-FR002")], total=9000)]
    out = pw.build(advance_src(pages, people, [prev("SB-FR003", {"advance": 3000})]), MONTH, TODAY)
    c = {p["code"][-1]: p["cells"]["advance"] for p in out["people"]}
    why = "รหัส SB-FR002 มี 2 บรรทัด อาจอ่านรหัสผิด"
    assert c["1"] == {"state": "ok", "value": 3000, "text": "3,000", "note": SAID}
    assert c["2"] == {"state": "carry", "value": 6000, "text": "6,000", "note": f"{SAID} · มีชื่อ 2 บรรทัด (3,000 + 3,000) อาจอ่านรหัสผิด ต้องตรวจกับใบเบิก"}
    assert c["3"] == {"state": "carry", "value": 3000, "text": "3,000", "note": f"ตามใบ HQ 10/2026 · ไม่มียอดเบิกในใบเบิกกลางเดือน แต่ยังยืนยันไม่ได้ว่าใบเบิกครบ ({why})"}
    assert c["4"]["state"] == "miss" and c["4"]["note"].endswith(f"({why})")                                          # on no HQ sheet either: a red gap, never a green 0
    assert not pw.advance_view([box(pages)], people)["branches"]["สระบุรี"]["complete"]
    assert file(out, "advance_list")["summary"] == f"สระบุรี อ่านแล้ว 3 คน 9,000 บาท · ยังไม่ได้กดลงบัญชีในกล่องรับไฟล์ · ยังยืนยันไม่ได้ว่าครบ ({why}) · แก่งคอยพักไว้ก่อน"
    # the same person found once by his code and once by his full name is a repeat as well
    again = [page(9, [drew("นายสอาด เถื่อนผา SB-FR002"), drew("นายสอาด เถื่อนผา SB-FO002")], total=6000)]
    view = pw.advance_view([box(again)], people)
    assert len(view["by_person"]["SB-FR002"]) == 2 and view["branches"]["สระบุรี"]["why"] == [why]


def test_a_code_taken_by_position_is_a_stand_in():
    # no line carries a code and the page's code list happens to be as long as the lines: who drew is then only a guess by position
    people = [emp("SB-FR001"), emp("SB-FR002"), emp("SB-FR003")]
    pages = [page(9, [drew("ข"), drew("ค")], total=6000, codes=["SB-FR001", "SB-FR002"])]
    out = pw.build(advance_src(pages, people, []), MONTH, TODAY)
    c = {p["code"][-1]: p["cells"]["advance"] for p in out["people"]}
    guess = {"state": "carry", "value": 3000, "text": "3,000", "note": f"{SAID} · ในบรรทัดไม่มีรหัส ใช้รหัสตามลำดับในหน้า ต้องตรวจกับใบเบิก"}
    assert c["1"] == guess == c["2"]
    assert c["3"]["state"] == "miss" and c["3"]["note"].endswith("(ช่าง / ออฟฟิศ / ฝ่ายผลิต: มี 2 บรรทัดที่ไม่มีรหัส ใช้รหัสตามลำดับในหน้า)")


def test_the_cover_page_must_agree_with_the_lists():
    # the cover writes the advances as one figure of its own: a list page that is not in the scan shows there, whatever the pages' totals say
    def cover(advance):
        return {**page(1, [], "รายการสรุปค่าใช้จ่าย สระบุรี", total=50000, kind_="cover"), "cover_parts": {"fuel": 20000, "advance": advance, "expense": 21000}}
    people = [emp("SB-FR001"), emp("SB-FR002"), emp("SB-FR003")]
    lists = [page(9, [drew("นาย ก SB-FR001")], total=3000), page(10, [drew("นาย ข SB-FR002")], total=3000)]      # a total per page: both add up
    out = pw.build(advance_src([cover(6000)] + lists, people, []), MONTH, TODAY)
    assert one(out, "SB-FR003")["cells"]["advance"] == {"state": "ok", "value": 0, "text": "0", "note": "ไม่มีชื่อในใบเบิกกลางเดือน — ไม่ได้เบิก"}
    assert pw.advance_lists([box([cover(6000)] + lists)])[0]["cover"] == 6000
    why = "ใบปกเขียนยอดเบิกกลางเดือน 9,000 แต่อ่านจากรายชื่อได้ 6,000"
    out = pw.build(advance_src([cover(9000)] + lists, people, []), MONTH, TODAY)                                     # a third page was never scanned
    assert one(out, "SB-FR001")["cells"]["advance"]["state"] == "ok"                                                 # his own line was read
    assert one(out, "SB-FR003")["cells"]["advance"]["state"] == "miss" and one(out, "SB-FR003")["cells"]["advance"]["note"].endswith(f"({why})")
    assert file(out, "advance_list")["summary"].endswith(f"ยังยืนยันไม่ได้ว่าครบ ({why}) · แก่งคอยพักไว้ก่อน")
    # a cover with no advance part (0), or as text with a comma, and a file without a cover
    assert pw.advance_view([box([cover(0)] + lists)], people)["branches"]["สระบุรี"]["complete"]
    assert pw.advance_view([box([cover("6,000")] + lists)], people)["branches"]["สระบุรี"]["complete"]
    assert pw.advance_view([box(lists)], people)["branches"]["สระบุรี"]["complete"]


def test_without_a_list_for_the_branch_nothing_changes():
    people = [emp("SB-FR001"), emp("SB-PD001", position="พนักงานขับรถ"), emp("SB-KK001", branch="แก่งคอย")]
    prevs = [prev(p["id"], {"advance": 3000}) for p in people]
    kk = box([page(6, [drew("SB-KK001 นายสายัณต์ พวนกิ่ง")], "บันทึกการทำงาน+เบิกกลางเดือน (แก่งคอย)", total=3000)], fid="k1", branch="แก่งคอย")
    out = pw.build(advance_src(None, people, prevs, advance_files=[kk]), MONTH, TODAY)
    stand_in = {"state": "carry", "value": 3000, "text": "3,000", "note": "ตามใบ HQ 10/2026 · รอรายชื่อเบิกจากเจ้าของ"}
    assert one(out, "SB-FR001")["cells"]["advance"] == stand_in == one(out, "SB-PD001")["cells"]["advance"]          # สระบุรี has no list: as before
    assert one(out, "SB-KK001")["cells"]["advance"]["state"] == "ok"
    assert [d["title"] for d in out["decisions"] if d["wait"]] == ["เบิกล่วงหน้า: รอรายชื่อจากเจ้าของ"]
    assert kind(out, "advance")["source"] == pw.TITLES_SOURCE["advance"]                                              # the list of a branch set aside is not the source
    # a file with the drivers' list alone says nothing about the factory's people
    only = box([page(7, [drew("นาย ก SB-PD001")], DRIVERS, total=3000)])
    out = pw.build(advance_src(None, people, prevs, advance_files=[only]), MONTH, TODAY)
    assert one(out, "SB-PD001")["cells"]["advance"]["state"] == "ok"
    assert one(out, "SB-FR001")["cells"]["advance"] == {"state": "carry", "value": 3000, "text": "3,000",
                                                        "note": "ตามใบ HQ 10/2026 · ใบเบิกกลางเดือนที่มีอยู่ไม่มีรายชื่อกลุ่มรหัส SB-FR — ยังยืนยันไม่ได้ว่าไม่ได้เบิก"}


def test_lines_that_find_nobody_on_the_pay_list():
    people = [emp("SB-PD001", "ทวาย กำจันทร์"), emp("SB-PD022", "ประสงค์ หลักทรัพย์"), emp("SB-PD030", "สำรวย อสุรินทร์"), emp("SB-PD031", "เอก ดีศรี"),
              emp("SB-FR005", "อัฐพล เปรมชื่น", pay_group="hq"), emp("SB-PD040", "คนเก่า ลาออก", status="resigned", end_date="2026-09-30"),
              emp("SB-PD041", "รอ เงินสะสม", status="resigned", end_date="2026-10-02"), emp("SB-PD050")]
    tail = [{"month": "2026-10", "code": "SB-PD041", "employee_id": "SB-PD041", "refund": 0, "savings_balance": 1500}]
    lines = [drew("นายทวาย กำจันทร์ SB-PD001"), drew("นายประสงค์ หลักทรัพย์ SB-PO022"), drew("นายสำรวย อสุรินทร์ SB-PD031"),
             drew("สุกัญญา บริรักษ์กุล SB-FR005", unsure=True), drew("นายคนเก่า ลาออก SB-PD040", 1000), drew("นายรอ เงินสะสม SB-PD041", 500)]
    file_name = f"{SRB}.pdf"
    out = pw.build(advance_src([page(7, lines, DRIVERS, total=13500)], people, [], pay_tail=tail), MONTH, TODAY)
    c = {p["code"]: p["cells"]["advance"] for p in out["people"]}
    assert c["SB-PD001"]["note"] == SAID
    # the reader got his code wrong: found by his full name, the only employee of that name
    assert c["SB-PD022"] == {"state": "ok", "value": 3000, "text": "3,000", "note": f"{SAID} · จับคู่จากชื่อ (รหัสในใบอ่านเป็น SB-PO022)"}
    # a code that is someone else's beside his full name: his by the name, to be checked -- and the code's own person is not told "nothing"
    assert c["SB-PD030"] == {"state": "carry", "value": 3000, "text": "3,000", "note": f"{SAID} · รหัสในใบ (SB-PD031) เป็นของคนอื่น จับคู่จากชื่อ ต้องตรวจ"}
    assert (c["SB-PD031"]["state"], c["SB-PD031"]["note"]) == ("miss", "ไม่มีใบ HQ ของ 5 เดือนล่าสุดให้ใช้เป็นค่าแทน · ในใบเบิกกลางเดือนมีรหัส SB-PD031 แต่ชื่อเป็น นายสำรวย อสุรินทร์ — ต้องตรวจว่าใครเบิก")
    assert c["SB-PD050"] == {"state": "ok", "value": 0, "text": "0", "note": "ไม่มีชื่อในใบเบิกกลางเดือน — ไม่ได้เบิก"}            # every line found an employee
    assert out["advance_unmatched"] == [
        {"who": "SB-FR005 สุกัญญา บริรักษ์กุล", "amount": 3000, "file": file_name, "why": "HQ จ่ายเอง · ในระบบ SB-FR005 คือ อัฐพล เปรมชื่น · อ่านไม่ชัด"},
        {"who": "SB-PD040 นายคนเก่า ลาออก", "amount": 1000, "file": file_name, "why": "ลาออกแล้ว (วันสุดท้าย 30/09/2026)"},
        {"who": "SB-PD041 นายรอ เงินสะสม", "amount": 500, "file": file_name, "why": "ลาออกแล้ว รอคืนเงินสะสม"}]
    # a line that finds no employee at all may be anyone who seems to be on no list: nobody is told "you drew nothing"
    lines += [drew("นายใหม่ ยังไม่มีในระบบ SB-PD099"), drew("นายไม่มีรหัส"), drew("นายแปลก SB-XX007")]
    out = pw.build(advance_src([page(7, lines, DRIVERS, total=22500)], people, [prev("SB-PD050")], pay_tail=tail), MONTH, TODAY)
    assert [(u["who"], u["why"]) for u in out["advance_unmatched"][3:]] == [("SB-PD099 นายใหม่ ยังไม่มีในระบบ", "ไม่มีรหัสนี้ในรายชื่อพนักงาน"),
                                                                           ("นายไม่มีรหัส", "ไม่มีรหัสพนักงานในบรรทัด"), ("SB-XX007 นายแปลก", "รหัส SB-XX007 ไม่ใช่รหัสพนักงาน")]
    assert one(out, "SB-PD050")["cells"]["advance"] == {"state": "carry", "value": 0, "text": "0", "note": "ใบ HQ 10/2026 ไม่ได้เบิก · ไม่มียอดเบิกในใบเบิกกลางเดือน "
                                                        "แต่ยังยืนยันไม่ได้ว่าใบเบิกครบ (พนักงานขับรถ: มี 3 บรรทัดที่จับคู่กับพนักงานไม่ได้)"}
    assert one(out, "SB-PD001")["cells"]["advance"]["state"] == "ok" and "จับคู่กับพนักงานไม่ได้" in file(out, "advance_list")["summary"]
    assert all(set(u) == {"who", "amount", "file", "why"} for u in out["advance_unmatched"])


def test_the_advance_list_line_booked_read_or_missing():
    pages = [page(9, [drew("นาย ก SB-FR001"), drew("นาย ข SB-FR002")], total=6000)]
    people = [emp("SB-FR001"), emp("SB-FR002")]
    waiting = [{"id": "f1", "kind": "scan", "status": "read", "month": MONTH, "branches": ["สระบุรี"]}]
    read = pw.build(advance_src(pages, people, [], inbox=waiting), MONTH, TODAY)
    f = file(read, "advance_list")
    assert (f["state"], f["link"], f["link_label"]) == ("warn", "#accounts-in", "ลงข้อมูล / แนบไฟล์")
    assert f["summary"] == "สระบุรี อ่านแล้ว 2 คน 6,000 บาท · ยังไม่ได้กดลงบัญชีในกล่องรับไฟล์ · แก่งคอยพักไว้ก่อน"      # the file itself is not "one more file waiting"
    assert "มี 1 ไฟล์สแกนในกล่องรับไฟล์ ยังไม่ได้ลง" in file(read, "month_papers")["summary"]
    assert all(p["cells"]["advance"]["state"] == "ok" for p in read["people"])                                       # read is enough for the cells
    booked = pw.build(advance_src(None, people, [], advance_files=[box(pages, status="booked")]), MONTH, TODAY)
    assert (file(booked, "advance_list")["state"], file(booked, "advance_list")["summary"]) == ("ok", "สระบุรี ลงบัญชีแล้ว 2 คน 6,000 บาท · แก่งคอยพักไว้ก่อน")
    none = pw.build(advance_src(None, people, []), MONTH, TODAY)
    assert (file(none, "advance_list")["state"], file(none, "advance_list")["summary"]) == ("bad", "ยังไม่ได้อัปโหลด")
    # pages booked the older way (no file in the drop box) still count, as before
    old = pw.build(advance_src(None, people, [], docs=ADVANCE_IN[:1]), MONTH, TODAY)
    assert (file(old, "advance_list")["state"], file(old, "advance_list")["summary"]) == ("ok", "สระบุรี 1 หน้า · แก่งคอยพักไว้ก่อน")
    assert pw.advance_line(pw.no_advance(), {"สระบุรี": 0, "แก่งคอย": 2}) == ("bad", "ยังไม่มี: สระบุรี · แก่งคอยพักไว้ก่อน 2 หน้า")


def test_the_advance_of_a_branch_on_hold():
    people = [emp("SB-FR001"), emp("SB-KK001", branch="แก่งคอย"), emp("SB-KK002", branch="แก่งคอย")]
    files = [box([page(9, [drew("นาย ก SB-FR001")], total=3000)], status="booked"),
             box([page(6, [drew("SB-KK001 นายสายัณต์ พวนกิ่ง"), drew("SB-KK099 นายใคร ไม่รู้")], "บันทึกการทำงาน+เบิกกลางเดือน (แก่งคอย)", total=6000)], fid="k1", branch="แก่งคอย")]
    s = advance_src(None, people, [prev("SB-KK002", {"advance": 3000})], advance_files=files)
    out = pw.build(s, MONTH, TODAY)
    # its people get their cells from its own list, and are counted nowhere
    k1, k2 = one(out, "SB-KK001"), one(out, "SB-KK002")
    assert k1["hold"] and k1["cells"]["advance"] == {"state": "ok", "value": 3000, "text": "3,000", "note": "ตามใบเบิกกลางเดือน [KK] รายการเบิกกลางเดือน15 พฤศจิกายน69"}
    assert k2["hold"] and k2["cells"]["advance"]["state"] == "carry" and "มี 1 บรรทัดที่จับคู่กับพนักงานไม่ได้" in k2["cells"]["advance"]["note"]
    k = kind(out, "advance")
    assert (k["need"], k["ok"], k["carry"], k["hold"], k["state"]) == (1, 1, 0, 2, "ok") and "แก่งคอย" not in k["source"]
    assert not any("เบิกล่วงหน้า" in d["title"] for d in out["decisions"])                                              # nothing is asked about a branch set aside
    f = file(out, "advance_list")                                                                                  # its part is said after "พักไว้ก่อน", and asks for nothing
    assert (f["state"], f["summary"]) == ("ok", "สระบุรี ลงบัญชีแล้ว 1 คน 3,000 บาท · แก่งคอยพักไว้ก่อน อ่านแล้ว 2 คน 6,000 บาท")
    assert [(u["who"], u["file"]) for u in out["advance_unmatched"]] == [("SB-KK099 นายใคร ไม่รู้", "[KK] รายการเบิกกลางเดือน15 พฤศจิกายน69.pdf")]


def test_the_advance_of_that_branch_without_the_hold(no_hold):
    people = [emp("SB-FR001"), emp("SB-KK001", branch="แก่งคอย"), emp("SB-KK002", branch="แก่งคอย")]
    files = [box([page(9, [drew("นาย ก SB-FR001")], total=3000)], status="booked"),
             box([page(6, [drew("SB-KK001 นายสายัณต์ พวนกิ่ง")], "บันทึกการทำงาน+เบิกกลางเดือน (แก่งคอย)", total=3000)], fid="k1", branch="แก่งคอย")]
    out = pw.build(advance_src(None, people, [], advance_files=files), MONTH, TODAY)
    k = kind(out, "advance")
    assert (k["need"], k["ok"], k["state"]) == (3, 3, "ok") and "hold" not in k and "แก่งคอย: บันทึกการทำงาน+เบิกกลางเดือน (แก่งคอย) 1 คน 3,000" in k["source"]
    f = file(out, "advance_list")
    assert (f["state"], f["summary"]) == ("warn", "สระบุรี ลงบัญชีแล้ว 1 คน 3,000 บาท · แก่งคอย อ่านแล้ว 1 คน 3,000 บาท · ยังไม่ได้กดลงบัญชีในกล่องรับไฟล์")
    only = pw.build(advance_src(None, people, [], advance_files=files[:1]), MONTH, TODAY)
    assert (file(only, "advance_list")["state"], file(only, "advance_list")["summary"]) == ("warn", "สระบุรี ลงบัญชีแล้ว 1 คน 3,000 บาท · ยังไม่มี: แก่งคอย")


def test_the_loader_reads_the_lists_from_the_drop_box(monkeypatch):
    from app import supabase
    seen = []
    row = {**box([page(9, [drew("นาย ก SB-FR001")], total=3000)]), "pages": [page(9, [drew("นาย ก SB-FR001")], total=3000)]}

    def fake(table, params=None, page=1000):
        seen.append((table, params))
        return [emp("SB-FR001")] if table == "employees" else [row] if params["select"] == pw.SELECTS["advance_files"] else []
    monkeypatch.setattr(supabase, "select_all", fake)
    s = pw._read(MONTH)
    asked = [p for t, p in seen if t == "inbox_files" and p["select"] == pw.SELECTS["advance_files"]]
    # only the scans of the month that were read, and of each only the named columns: the pages the AI read, nothing else of the preview
    assert asked == [{"select": pw.SELECTS["advance_files"], "month": "eq.2026-11", "kind": "eq.scan", "status": "in.(read,booked)", "order": "uploaded_at.desc,id.asc"}]
    cols = [c.split(":")[-1] for c in pw.SELECTS["advance_files"].split(",")]
    assert cols == ["id", "file_name", "kind", "status", "month", "branches", "uploaded_at", "booked_at", "preview->half", "preview->ai_pages"]
    assert s["failed"] == [] and one(pw.build(s, MONTH, TODAY), "SB-FR001")["cells"]["advance"]["state"] == "ok"

    def down(table, params=None, page=1000):
        if params["select"] == pw.SELECTS["advance_files"]:
            raise supabase.SupabaseError("boom")
        return [emp("SB-FR001")] if table == "employees" else []
    monkeypatch.setattr(supabase, "select_all", down)
    s = pw._read(MONTH)
    assert s["failed"] == ["ใบเบิกกลางเดือน"] and s["advance_files"] is None and file(pw.build(s, MONTH, TODAY), "advance_list")["summary"] == "โหลดไม่สำเร็จ"


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
    assert out["rules"] == {"general": [], "groups": [], "source": pw.RULES_SOURCE} and out["hold_branches"] == ["แก่งคอย"]
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
                                    "attendance_uploads", "employee_loans", "employee_loan_entries", "acc_entries", "cash_fuel_lines", "vehicles", "drivers"}
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


def test_the_sso_list_replaces_the_stand_in():
    # owner, 27 Nov 2026: the สปส.1-10 list gives each person's ปกส.; people not on it keep last sheet's figure, said in amber
    people = [emp("A"), emp("B"), emp("C")]
    rules = [{**rule("A"), "sso": 875, "sso_source": "สปส.1-10 ก.ย. 2569"}, {**rule("B"), "sso": 0, "sso_source": "สปส.1-10 ก.ย. 2569"}, rule("C")]
    prevs = [prev("A", {"sso": 750}), prev("B", {"sso": 429}), prev("C", {"sso": 625})]
    out = pw.build(src(employees=people, rules=rules, rates=[rate(p["id"]) for p in people], pay_prev=prevs), MONTH, TODAY)
    a, b, c = (one(out, x)["cells"]["sso"] for x in "ABC")
    assert a == {"state": "ok", "value": 875, "text": "875", "note": "ตาม สปส.1-10 ก.ย. 2569"}
    assert b == {"state": "ok", "value": 0, "text": "0", "note": "ตาม สปส.1-10 ก.ย. 2569 · ไม่หัก"}
    assert c["state"] == "carry" and c["value"] == 625 and c["note"].startswith("ไม่มีชื่อใน สปส.1-10 ก.ย. 2569 · ตามใบ HQ")
