from datetime import date, timedelta

import pytest

from app import service_plan as sp

TODAY = date(2026, 10, 22)
VID = "11111111-1111-1111-1111-111111111111"
SMALL_TRUCK = {"id": VID, "vehicle_class": sp.SMALL}
BIG_TRUCK = {"id": VID, "vehicle_class": sp.BIG}


def item(key="engine_oil", km=10_000, months=6):
    return {"key": key, "label": sp.item_label(key), "group": "x", "interval_km": km, "interval_months": months}


def rec(key, when, odo=None, source="manual"):
    return {"item_key": key, "done_date": when, "odometer_km": odo, "source": source}


# ------------------------------------------------------------ the plan
def test_every_default_item_exists_and_has_a_positive_interval():
    for cls, plan in sp.DEFAULT_PLAN.items():
        for key, (km, months) in plan.items():
            assert key in sp.ITEMS
            assert km > 0 and months > 0


def test_small_and_big_follow_the_dealership_rules():
    small, big = sp.DEFAULT_PLAN[sp.SMALL], sp.DEFAULT_PLAN[sp.BIG]
    assert small["engine_oil"][0] == 10_000 and big["engine_oil"][0] == 20_000
    assert small["engine_filter"][0] == 10_000          # the owner's choice, not the dealership's 20,000
    assert small["wheel_grease"][0] == 30_000 and big["wheel_grease"][0] == 60_000
    assert small["fuel_filter"][0] == 50_000 and big["fuel_filter"][0] == 20_000
    assert small["air_filter"][0] == 40_000 and big["air_filter"][0] == 80_000
    assert "injector_washers" in small and "injector_washers" not in big
    for key in ("gearbox_oil", "diff_oil", "brake_clutch_fluid", "power_steering", "coolant", "valve_adjust"):
        assert small[key][0] == 40_000 and big[key][0] == 40_000


def test_effective_plan_applies_overrides_for_that_class_only():
    over = [{"vehicle_class": sp.BIG, "item_key": "engine_oil", "interval_km": 15_000, "interval_months": 4}]
    big = {i["key"]: i for i in sp.effective_plan(sp.BIG, over)}
    small = {i["key"]: i for i in sp.effective_plan(sp.SMALL, over)}
    assert big["engine_oil"]["interval_km"] == 15_000 and big["engine_oil"]["interval_months"] == 4 and big["engine_oil"]["customised"]
    assert small["engine_oil"]["interval_km"] == 10_000 and not small["engine_oil"]["customised"]


def test_unknown_class_has_no_plan():
    assert sp.effective_plan(None) == [] and sp.effective_plan("รถบัส") == []


# ------------------------------------------------------- reading bill lines
@pytest.mark.parametrize("text,key", [
    ("น้ำมันเครื่อง", "engine_oil"), ("ถ่ายน้ำมันเครื่อง", "engine_oil"), ("น้ำมันเครื่อง 15W-40", "engine_oil"),
    ("กรองเครื่อง", "engine_filter"), ("กรองน้ำมันเครื่อง", "engine_filter"), ("ไส้กรองเครื่อง", "engine_filter"),
    ("กรองโซล่า", "fuel_filter"), ("กรองดักน้ำ", "fuel_filter"), ("กรองอากาศ", "air_filter"),
    ("น้ำมันเกียร์", "gearbox_oil"), ("น้ำมันเฟืองท้าย", "diff_oil"),
    ("น้ำมันเบรคใหญ่", "brake_clutch_fluid"), ("น้ำมันเบรค + คลัช", "brake_clutch_fluid"), ("น้ำมันคลัช", "brake_clutch_fluid"),
    ("น้ำมันพาวเวอร์", "power_steering"), ("น้ำมันเพาเวอร์", "power_steering"),
    ("น้ำยาหล่อเย็นหม้อน้ำ", "coolant"), ("น้ำยาหม้อน้ำ", "coolant"), ("น้ำยาหล่อเย็น(สูตรผสมน้ำพร้อมใช้) 5 ลิตร(ตรีเพชร)(ใหม่)-BP", "coolant"),
    ("จาระบีล้อ", "wheel_grease"), ("ตั้งวาล์ว", "valve_adjust"),
])
def test_bill_lines_that_are_a_service(text, key):
    assert sp.classify_line(text) == key


@pytest.mark.parametrize("text", [
    "น้ำมันเครื่อง (เติม)",                            # a top-up
    "สกรูแรงดันน้ำมันเครื่องข้างเสื้อสูบ", "เข็มขัดรัดท่อยางน้ำมันพาวเวอร์", "เหล็กยึดท่อกรองอากาศ",   # parts, not the service
    "น้ำมันล้าง", "อัดจาระบี", "หัวอัดจาระบี", "เปลี่ยนหม้อน้ำ", "ลูกยางหม้อน้ำ(อีซูซุ)(ใหม่)-BP", "ค่าแรงเปลี่ยนถ่าย", "", None,
])
def test_bill_lines_that_are_not(text):
    assert sp.classify_line(text) is None


def test_bill_hits_one_per_truck_item_and_day():
    events = [{"id": "e1", "vehicle_id": VID, "event_date": "2026-10-22", "mileage": 54000,
               "bill_line_items": [{"description": "กรองเครื่อง"}, {"description": "กรองเครื่อง"}, {"description": "น้ำมันเครื่อง"}, {"description": "ค่าแรง"}]}]
    hits = sp.bill_hits(events)
    assert sorted(h["item_key"] for h in hits) == ["engine_filter", "engine_oil"]
    assert all(h["source"] == "bill" and h["odometer_km"] == 54000 and h["done_date"] == "2026-10-22" for h in hits)


# ------------------------------------------------------------ the maths
def test_add_months_clamps_to_the_end_of_the_month():
    assert sp.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert sp.add_months(date(2026, 10, 22), 6) == date(2027, 4, 22)
    assert sp.add_months(date(2026, 10, 22), 24) == date(2028, 10, 22)


def test_km_per_day_falls_back_to_the_class_average():
    e = sp.estimate_km_per_day([], TODAY, sp.SMALL)
    assert e["source"] == "default" and e["value"] == sp.DEFAULT_KM_PER_DAY[sp.SMALL]
    assert sp.estimate_km_per_day([], TODAY, None)["value"] is None


def test_km_per_day_from_gps_periods():
    readings = [{"kind": "gps", "start_date": "2026-10-01", "end_date": "2026-10-22", "km": 3949}]    # 22 days
    e = sp.estimate_km_per_day(readings, TODAY, sp.SMALL)
    assert e["source"] == "gps" and e["value"] == pytest.approx(179.5, abs=0.1)


def test_old_gps_periods_are_ignored():
    readings = [{"kind": "gps", "start_date": "2025-01-01", "end_date": "2025-01-31", "km": 9000}]
    assert sp.estimate_km_per_day(readings, TODAY, sp.BIG)["source"] == "default"


def test_odometer_wins_when_the_readings_are_far_enough_apart():
    readings = [{"kind": "odometer", "end_date": "2026-08-22", "km": 50_000}, {"kind": "odometer", "end_date": "2026-10-22", "km": 56_100},
                {"kind": "gps", "start_date": "2026-10-01", "end_date": "2026-10-22", "km": 4400}]
    e = sp.estimate_km_per_day(readings, TODAY, sp.SMALL)
    assert e["source"] == "odometer" and e["value"] == pytest.approx(100.0)


def test_close_odometer_readings_do_not_beat_gps():
    readings = [{"kind": "odometer", "end_date": "2026-10-15", "km": 50_000}, {"kind": "odometer", "end_date": "2026-10-22", "km": 50_700},
                {"kind": "gps", "start_date": "2026-10-01", "end_date": "2026-10-22", "km": 2200}]
    assert sp.estimate_km_per_day(readings, TODAY, sp.SMALL)["source"] == "gps"


def test_last_done_takes_the_newest_and_prefers_a_typed_record_on_a_tie():
    rows = [rec("engine_oil", "2026-06-01"), rec("engine_oil", "2026-09-01", source="bill"), rec("engine_oil", "2026-09-01", odo=5, source="manual"),
            rec("gearbox_oil", "2026-10-01")]
    d = sp.last_done("engine_oil", rows)
    assert d["date"] == date(2026, 9, 1) and d["source"] == "manual" and d["odometer_km"] == 5
    assert sp.last_done("diff_oil", rows) is None


def test_item_with_no_history_is_unknown():
    s = sp.item_status(item(), None, 130, None, TODAY)
    assert s["state"] == "unknown" and s["due_date"] is None


def test_months_deadline_wins_for_a_truck_that_barely_moves():
    s = sp.item_status(item(km=20_000, months=6), {"date": date(2026, 5, 1), "source": "manual"}, 55, None, TODAY)   # 55 km/day: 363 days for 20,000
    assert s["due_by"] == "months" and s["due_date"] == "2026-11-01" and s["days_left"] == 10 and s["state"] == "soon"


def test_km_deadline_wins_for_a_busy_truck():
    s = sp.item_status(item(km=10_000, months=6), {"date": date(2026, 9, 1), "source": "manual"}, 180, None, TODAY)    # 51 days x 180 = 9180 km
    assert s["due_by"] == "km" and s["km_since"] == 9180 and s["km_left"] == 820 and s["km_basis"] == "estimate"
    assert s["due_date"] == (TODAY + timedelta(days=5)).isoformat() and s["state"] == "soon"


def test_overdue_by_km_and_by_months():
    by_km = sp.item_status(item(km=10_000, months=6), {"date": date(2026, 8, 1), "source": "manual"}, 180, None, TODAY)   # 82 days x 180 = 14,760
    assert by_km["state"] == "overdue" and by_km["km_left"] < 0
    by_months = sp.item_status(item(km=20_000, months=6), {"date": date(2026, 3, 1), "source": "manual"}, 10, None, TODAY)
    assert by_months["state"] == "overdue" and by_months["due_by"] == "months"


def test_a_fresh_service_is_ok():
    s = sp.item_status(item(), {"date": TODAY, "source": "bill"}, 130, None, TODAY)
    assert s["state"] == "ok" and s["km_since"] == 0 and s["days_left"] > 60


def test_real_odometer_beats_the_estimate():
    odo = {"date": TODAY, "km": 60_000}
    s = sp.item_status(item(km=10_000, months=6), {"date": date(2026, 9, 1), "odometer_km": 55_000, "source": "manual"}, 180, odo, TODAY)
    assert s["km_basis"] == "odometer" and s["km_since"] == 5000 and s["km_left"] == 5000


def test_an_old_odometer_is_carried_forward_with_the_daily_distance():
    odo = {"date": TODAY - timedelta(days=10), "km": 60_000}
    s = sp.item_status(item(km=10_000, months=6), {"date": date(2026, 9, 1), "odometer_km": 55_000, "source": "manual"}, 100, odo, TODAY)
    assert s["km_since"] == 6000


def test_no_distance_data_at_all_leaves_only_the_months_limit():
    s = sp.item_status(item(km=10_000, months=6), {"date": date(2026, 9, 1), "source": "manual"}, None, None, TODAY)
    assert s["due_by"] == "months" and s["km_since"] is None


def test_km_only_item_without_distance_data_is_ok_but_undated():
    s = sp.item_status(item(km=10_000, months=None), {"date": date(2026, 9, 1), "source": "manual"}, None, None, TODAY)
    assert s["state"] == "ok" and s["due_date"] is None


def test_vehicle_plan_sorts_the_worst_first_and_counts():
    records = [rec("engine_oil", "2026-06-01"), rec("engine_filter", "2026-10-20")]
    plan = sp.vehicle_plan(SMALL_TRUCK, records=records, readings=[], overrides=[], today=TODAY)
    assert plan["items"][0]["key"] == "engine_oil" and plan["items"][0]["state"] == "overdue"
    assert plan["counts"]["overdue"] == 1 and plan["counts"]["ok"] == 1 and plan["counts"]["unknown"] == len(plan["items"]) - 2
    assert plan["worst"] == "overdue" and plan["has_plan"]
    assert plan["km_per_day"]["source"] == "default"


def test_vehicle_without_a_class_has_no_plan():
    plan = sp.vehicle_plan({"id": VID, "vehicle_class": None}, records=[], readings=[], overrides=[], today=TODAY)
    assert plan["items"] == [] and plan["worst"] is None and not plan["has_plan"]


# ------------------------------------------------------------ validation
def test_clean_records_makes_one_row_per_item():
    rows = sp.clean_records({"vehicle_id": VID, "items": ["engine_oil", "engine_filter", "engine_oil"], "done_date": "2026-10-20", "odometer_km": "54,000", "notes": " ที่อู่ A "}, today=TODAY)
    assert [r["item_key"] for r in rows] == ["engine_oil", "engine_filter"]
    assert all(r["odometer_km"] == 54000 and r["notes"] == "ที่อู่ A" and r["source"] == "manual" for r in rows)


@pytest.mark.parametrize("patch,message", [
    ({"items": []}, "อย่างน้อย"), ({"items": ["nope"]}, "ไม่รู้จัก"), ({"vehicle_id": "x"}, "รถ"),
    ({"done_date": "2026-10-23"}, "ยังไม่ถึง"), ({"done_date": "2569-10-20"}, "พ.ศ."), ({"done_date": ""}, "ไม่ถูกต้อง"),
    ({"odometer_km": "abc"}, "ตัวเลข"), ({"odometer_km": "-5"}, "ไม่ถูกต้อง"),
])
def test_clean_records_rejects(patch, message):
    data = {"vehicle_id": VID, "items": ["engine_oil"], "done_date": "2026-10-20", **patch}
    with pytest.raises(sp.InvalidPlan, match=message):
        sp.clean_records(data, today=TODAY)


def test_clean_records_baseline_flag():
    rows = sp.clean_records({"vehicle_id": VID, "items": ["engine_oil"], "done_date": "2026-10-22", "source": "baseline"}, today=TODAY)
    assert rows[0]["source"] == "baseline"


def test_clean_reading_gps_and_odometer():
    g = sp.clean_reading({"vehicle_id": VID, "kind": "gps", "start_date": "2026-10-01", "end_date": "2026-10-22", "km": "3949"}, today=TODAY)
    assert g["start_date"] == "2026-10-01" and g["km"] == 3949
    o = sp.clean_reading({"vehicle_id": VID, "kind": "odometer", "end_date": "2026-10-22", "km": 56100}, today=TODAY)
    assert o["start_date"] is None and o["km"] == 56100


@pytest.mark.parametrize("patch", [
    {"kind": "x"}, {"km": ""}, {"km": "-1"}, {"start_date": "2026-10-23"}, {"start_date": "2024-01-01"}, {"end_date": "2026-10-23"},
])
def test_clean_reading_rejects(patch):
    data = {"vehicle_id": VID, "kind": "gps", "start_date": "2026-10-01", "end_date": "2026-10-22", "km": 3949, **patch}
    with pytest.raises(sp.InvalidPlan):
        sp.clean_reading(data, today=TODAY)


def test_clean_interval():
    row = sp.clean_interval({"vehicle_class": sp.BIG, "item_key": "engine_oil", "interval_km": "15000", "interval_months": ""})
    assert row == {"vehicle_class": sp.BIG, "item_key": "engine_oil", "interval_km": 15000, "interval_months": None}
    with pytest.raises(sp.InvalidPlan):
        sp.clean_interval({"vehicle_class": sp.BIG, "item_key": "engine_oil", "interval_km": "", "interval_months": ""})
    with pytest.raises(sp.InvalidPlan):
        sp.clean_interval({"vehicle_class": sp.BIG, "item_key": "injector_washers", "interval_km": 1000})      # big trucks have no such item
    with pytest.raises(sp.InvalidPlan):
        sp.clean_interval({"vehicle_class": "รถบัส", "item_key": "engine_oil", "interval_km": 1000})
