import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import calendar_plan as cp

TODAY = date(2026, 11, 5)


def item(key, label, state, days_left=None, due=None, months=None, **kw):
    return {"key": key, "label": label, "state": state, "days_left": days_left, "due_date": due, "interval_months": months, **kw}


def truck(items, **kw):
    return {"id": "v1", "vehicle_number": "13", "plate_number": "1ฒณ 2086", "branch": "แก่งคอย", "has_plan": True, "items": items, **kw}


def test_the_oil_change_sets_the_day_and_the_rest_rides_along():
    row = truck([item("engine_oil", "น้ำมันเครื่อง", "soon", 9, "2026-11-14", 2),
                 item("gear_oil", "น้ำมันเกียร์", "overdue", -165, "2026-05-24"),             # late on its own: comes with the oil change
                 item("oil_filter", "กรองน้ำมันเครื่อง", "ok", 40, "2026-12-15"),            # before the next oil change (~70 days): now
                 item("coolant", "น้ำยาหล่อเย็น", "ok", 300, "2027-09-01"),                  # well after: not this time
                 item("air_filter", "กรองอากาศ", "unknown")],
                history_current_on="2026-11-01")
    v = cp.truck_visit(row, TODAY)
    assert v["date"] == "2026-11-14" and v["tone"] == "soon" and v["label"] == "#13 · 1ฒณ 2086"
    assert v["together"] == ["น้ำมันเกียร์", "กรองน้ำมันเครื่อง"] and v["maybe"] == ["กรองอากาศ"]


def test_no_green_tick_no_maybe_and_no_oil_record_no_date():
    row = truck([item("engine_oil", "น้ำมันเครื่อง", "ok", 30, "2026-12-05", 2), item("air_filter", "กรองอากาศ", "unknown")])
    assert cp.truck_visit(row, TODAY)["maybe"] == []                     # history not complete: "no record" says nothing
    grey = cp.truck_visit(truck([item("engine_oil", "น้ำมันเครื่อง", "unknown"), item("gear_oil", "น้ำมันเกียร์", "overdue", -3)]), TODAY)
    assert grey["date"] is None and grey["tone"] == "none" and grey["together"] == []
    assert cp.truck_visit(truck([], has_plan=False), TODAY) is None


def test_a_snoozed_oil_change_sits_on_the_day_it_was_put_off_to():
    v = cp.truck_visit(truck([item("engine_oil", "น้ำมันเครื่อง", "overdue", -2, "2026-11-03", 2, snoozed_until="2026-11-20")]), TODAY)
    assert v["date"] == "2026-11-20" and v["days_left"] == 15 and v["tone"] == "later"


def test_machines_get_a_visit_and_their_own_date_jobs():
    m = {"id": "m1", "name": "เครื่อง 1", "plan": {
        "jobs": [{"key": "oil", "name": "ถ่ายน้ำมันคอมเพรสเซอร์", "with_oil": False, "due_date": "2026-11-30", "days_left": 25},
                 {"key": "grease", "name": "จาระบีมอเตอร์คอมเพรสเซอร์", "with_oil": True, "due_date": "2026-11-30", "days_left": 25},
                 {"key": "belts", "name": "เช็คและตั้งสายพานคอมเพรสเซอร์", "with_oil": True, "due_date": None, "days_left": None},
                 {"key": "condenser", "name": "ล้างคอนเดนเซอร์", "with_oil": False, "due_date": "2026-11-10", "days_left": 5}],
        "visit": {"date": "2026-11-30", "days_left": 25, "state": "soon", "together": ["grease"], "maybe": ["belts"]}}}
    out = cp.machine_entries(m, TODAY)
    assert [(e["main"], e["date"], e["tone"]) for e in out] == [("ถ่ายน้ำมันคอมเพรสเซอร์", "2026-11-30", "later"), ("ล้างคอนเดนเซอร์", "2026-11-10", "soon")]
    assert out[0]["together"] == ["จาระบีมอเตอร์คอมเพรสเซอร์"] and out[0]["maybe"] == ["เช็คและตั้งสายพานคอมเพรสเซอร์"]


def test_build_sorts_by_day_and_keeps_the_undated_apart():
    rows = [truck([item("engine_oil", "น้ำมันเครื่อง", "ok", 20, "2026-11-25", 2)], id="a"),
            truck([item("engine_oil", "น้ำมันเครื่อง", "late", -4, "2026-11-01", 2)], id="b"),
            truck([item("engine_oil", "น้ำมันเครื่อง", "unknown")], id="c")]
    d = cp.build(rows, [], TODAY)
    assert [e["id"] for e in d["entries"]] == ["b", "a"] and d["entries"][0]["tone"] == "late"
    assert [x["id"] for x in d["no_date"]] == ["c"]
