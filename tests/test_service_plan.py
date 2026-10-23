from datetime import date, timedelta

import pytest

from app import service_plan as sp

TODAY = date(2026, 10, 22)
VID = "11111111-1111-1111-1111-111111111111"
TRUCK = {"id": VID}
GROUPS = sp.merge_groups(None)
S2, S3, B5, B6, KK1 = (GROUPS[c] for c in ("S2", "S3", "B5", "B6", "KK1"))


def item(key="engine_oil", km=10_000, months=2.0):
    return {"key": key, "label": sp.item_label(key), "group": "x", "interval_km": km, "interval_months": months}


def rec(key, when, odo=None, source="manual"):
    return {"item_key": key, "done_date": when, "odometer_km": odo, "source": source}


# ------------------------------------------------------------ the plan
def test_the_dealership_km_for_small_and_big():
    small, big = sp.DEFAULT_KM[sp.SMALL], sp.DEFAULT_KM[sp.BIG]
    assert (small["engine_oil"], big["engine_oil"]) == (10_000, 20_000)
    assert (small["engine_filter"], big["engine_filter"]) == (10_000, 20_000)
    assert (small["fuel_filter"], big["fuel_filter"]) == (40_000, 20_000)
    assert (small["air_filter"], big["air_filter"]) == (40_000, 80_000)
    assert (small["wheel_grease"], big["wheel_grease"]) == (30_000, 60_000)
    for key in ("gearbox_oil", "diff_oil", "brake_clutch_fluid", "power_steering", "coolant"):
        assert small[key] == 40_000 and big[key] == 40_000
    assert "valve_adjust" not in small and big["valve_adjust"] == 80_000          # D-Max has hydraulic lifters
    assert small["injector_washers"] == 100_000 and "injector_washers" not in big
    assert big["valve_cover_seal"] == 80_000 and "valve_cover_seal" not in small


def test_every_default_item_exists():
    for table in sp.DEFAULT_KM.values():
        for key, km in table.items():
            assert key in sp.ITEMS and (km is None and key in sp.FIXED_MONTHS or km > 0)


def test_group_months_and_implied_km_per_day():
    assert sp.group_months(S2) == 2 and sp.group_months(B6) == 6
    assert sp.group_km_per_day(S2) == pytest.approx(164.5, abs=0.5)         # 10,000 km in 2 months
    assert sp.group_months(KK1) == 2.7                                       # 20,000 / (242.3 x 30.4)
    assert sp.group_km_per_day(KK1) == pytest.approx(243.7, abs=1)


def test_the_owners_conversion_table():
    """2 months = 10k  ->  20k = 4, 30k = 6, 40k = 8, 100k = 20 months; 5 months = 20k -> 40k = 10, 80k = 20."""
    s2 = {i["key"]: i["interval_months"] for i in sp.effective_plan(S2)}
    assert (s2["engine_oil"], s2["wheel_grease"], s2["coolant"], s2["injector_washers"]) == (2, 6, 8, 20)
    s3 = {i["key"]: i["interval_months"] for i in sp.effective_plan(S3)}
    assert (s3["engine_oil"], s3["wheel_grease"], s3["coolant"], s3["injector_washers"]) == (3, 9, 12, 30)
    b5 = {i["key"]: i["interval_months"] for i in sp.effective_plan(B5)}
    assert (b5["engine_oil"], b5["gearbox_oil"], b5["wheel_grease"], b5["air_filter"]) == (5, 10, 15, 20)
    b6 = {i["key"]: i["interval_months"] for i in sp.effective_plan(B6)}
    assert (b6["engine_oil"], b6["gearbox_oil"], b6["wheel_grease"], b6["air_filter"]) == (6, 12, 18, 24)


def test_effective_plan_uses_the_rule_set_of_the_group():
    assert "injector_washers" in {i["key"] for i in sp.effective_plan(S2)}
    assert "injector_washers" not in {i["key"] for i in sp.effective_plan(B5)}
    assert "valve_adjust" not in {i["key"] for i in sp.effective_plan(S2)} and "valve_adjust" in {i["key"] for i in sp.effective_plan(B5)}


def test_saved_km_changes_apply_to_that_rule_set_only():
    over = [{"vehicle_class": sp.BIG, "item_key": "engine_oil", "interval_km": 15_000, "interval_months": None}]
    big = {i["key"]: i for i in sp.effective_plan(B6, over)}
    small = {i["key"]: i for i in sp.effective_plan(S2, over)}
    assert big["engine_oil"]["interval_km"] == 15_000 and big["engine_oil"]["interval_months"] == 4.5 and big["engine_oil"]["customised"]
    assert small["engine_oil"]["interval_km"] == 10_000 and not small["engine_oil"]["customised"]


def test_no_group_means_no_plan():
    assert sp.effective_plan(None) == []


def test_saved_group_rows_win_over_the_defaults():
    merged = sp.merge_groups([{"code": "S2", "months_per_step": 3}])
    assert sp.group_months(merged["S2"]) == 3 and merged["S2"]["step_km"] == 10_000
    assert sp.group_months(merged["S3"]) == 3 and "KK1" in merged


# ------------------------------------------------------- reading bill lines
@pytest.mark.parametrize("text,key", [
    ("น้ำมันเครื่อง", "engine_oil"), ("ถ่ายน้ำมันเครื่อง", "engine_oil"), ("น้ำมันเครื่อง 15W-40", "engine_oil"),
    ("กรองเครื่อง", "engine_filter"), ("กรองน้ำมันเครื่อง", "engine_filter"), ("ไส้กรองเครื่อง", "engine_filter"),
    ("กรองโซล่า", "fuel_filter"), ("กรองดักน้ำ", "fuel_filter"), ("กรองอากาศ", "air_filter"),
    ("น้ำมันเกียร์", "gearbox_oil"), ("น้ำมันเฟืองท้าย", "diff_oil"),
    ("น้ำมันเบรคใหญ่", "brake_clutch_fluid"), ("น้ำมันเบรค + คลัช", "brake_clutch_fluid"), ("น้ำมันคลัช", "brake_clutch_fluid"),
    ("น้ำมันพาวเวอร์", "power_steering"), ("น้ำมันเพาเวอร์", "power_steering"),
    ("น้ำยาหล่อเย็นหม้อน้ำ", "coolant"), ("น้ำยาหม้อน้ำ", "coolant"), ("น้ำยาหล่อเย็น(สูตรผสมน้ำพร้อมใช้) 5 ลิตร(ตรีเพชร)(ใหม่)-BP", "coolant"),
    ("จาระบีล้อ", "wheel_grease"), ("ตั้งวาล์ว", "valve_adjust"), ("ประเก็นฝาวาล์ว", "valve_cover_seal"), ("ซีลฝาครอบหัวฉีด", "valve_cover_seal"),
    ("แหวนรองหัวฉีด", "injector_washers"),
])
def test_bill_lines_that_are_a_service(text, key):
    assert sp.classify_line(text) == key


@pytest.mark.parametrize("text", [
    "น้ำมันเครื่อง (เติม)",                            # a top-up
    "สกรูแรงดันน้ำมันเครื่องข้างเสื้อสูบ", "เข็มขัดรัดท่อยางน้ำมันพาวเวอร์", "เหล็กยึดท่อกรองอากาศ",   # parts, not the service
    "น้ำมันล้าง", "หัวอัดจาระบี", "เปลี่ยนหม้อน้ำ", "ลูกยางหม้อน้ำ(อีซูซุ)(ใหม่)-BP", "ค่าแรงเปลี่ยนถ่าย", "", None,
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


def test_fractional_months_become_days():
    assert sp.add_months_f(date(2026, 10, 22), 2) == date(2026, 12, 22)
    assert sp.add_months_f(date(2026, 10, 1), 2.7) == date(2026, 12, 22)        # 2 months and 21 days


def test_km_per_day_falls_back_to_what_the_group_assumes():
    e = sp.estimate_km_per_day([], TODAY, 164.5)
    assert e["source"] == "default" and e["value"] == 164.5
    assert sp.estimate_km_per_day([], TODAY, None)["value"] is None


def test_km_per_day_from_gps_periods():
    readings = [{"kind": "gps", "start_date": "2026-10-01", "end_date": "2026-10-22", "km": 3949}]    # 22 days
    e = sp.estimate_km_per_day(readings, TODAY, 100)
    assert e["source"] == "gps" and e["value"] == pytest.approx(179.5, abs=0.1)


def test_old_gps_periods_are_ignored():
    readings = [{"kind": "gps", "start_date": "2025-01-01", "end_date": "2025-01-31", "km": 9000}]
    assert sp.estimate_km_per_day(readings, TODAY, 70)["source"] == "default"


def test_odometer_wins_when_the_readings_are_far_enough_apart():
    readings = [{"kind": "odometer", "end_date": "2026-08-22", "km": 50_000}, {"kind": "odometer", "end_date": "2026-10-22", "km": 56_100},
                {"kind": "gps", "start_date": "2026-10-01", "end_date": "2026-10-22", "km": 4400}]
    e = sp.estimate_km_per_day(readings, TODAY, 100)
    assert e["source"] == "odometer" and e["value"] == pytest.approx(100.0)


def test_close_odometer_readings_do_not_beat_gps():
    readings = [{"kind": "odometer", "end_date": "2026-10-15", "km": 50_000}, {"kind": "odometer", "end_date": "2026-10-22", "km": 50_700},
                {"kind": "gps", "start_date": "2026-10-01", "end_date": "2026-10-22", "km": 2200}]
    assert sp.estimate_km_per_day(readings, TODAY, 100)["source"] == "gps"


def test_last_done_takes_the_newest_and_prefers_a_typed_record_on_a_tie():
    rows = [rec("engine_oil", "2026-06-01"), rec("engine_oil", "2026-09-01", source="bill"), rec("engine_oil", "2026-09-01", odo=5, source="manual"),
            rec("gearbox_oil", "2026-10-01")]
    d = sp.last_done("engine_oil", rows)
    assert d["date"] == date(2026, 9, 1) and d["source"] == "manual" and d["odometer_km"] == 5
    assert sp.last_done("diff_oil", rows) is None


def test_item_with_no_history_is_unknown():
    s = sp.item_status(item(), None, None, None, TODAY)
    assert s["state"] == "unknown" and s["due_date"] is None


def test_months_decide_when_no_distance_is_measured():
    s = sp.item_status(item(km=10_000, months=2), {"date": date(2026, 9, 1), "source": "manual"}, None, None, TODAY)
    assert s["due_by"] == "months" and s["due_date"] == "2026-11-01" and s["days_left"] == 10 and s["state"] == "soon"
    assert s["km_since"] is None


def test_measured_driving_can_bring_a_due_date_forward():
    # the owner's months say 2 per 10k, but this truck really does 250 km/day: 40 days x 250 = 10,000 km already
    s = sp.item_status(item(km=10_000, months=2), {"date": date(2026, 9, 12), "source": "manual"}, 250, None, TODAY)
    assert s["due_by"] == "km" and s["state"] in ("overdue", "soon")
    assert s["km_since"] == 10_000 and s["km_basis"] == "estimate"


def test_overdue_by_months():
    s = sp.item_status(item(km=10_000, months=2), {"date": date(2026, 8, 1), "source": "manual"}, None, None, TODAY)
    assert s["state"] == "overdue" and s["due_by"] == "months" and s["days_left"] < 0


def test_a_fresh_service_is_ok():
    s = sp.item_status(item(), {"date": TODAY, "source": "bill"}, None, None, TODAY)
    assert s["state"] == "ok" and s["days_left"] > 50


def test_real_odometer_gives_the_km_used_and_left():
    odo = {"date": TODAY, "km": 60_000}
    s = sp.item_status(item(km=10_000, months=6), {"date": date(2026, 9, 1), "odometer_km": 55_000, "source": "manual"}, None, odo, TODAY)
    assert s["km_basis"] == "odometer" and s["km_since"] == 5000 and s["km_left"] == 5000


def test_an_overdue_odometer_reading_is_overdue_even_without_a_daily_rate():
    odo = {"date": TODAY, "km": 70_000}
    s = sp.item_status(item(km=10_000, months=6), {"date": date(2026, 9, 1), "odometer_km": 55_000, "source": "manual"}, None, odo, TODAY)
    assert s["state"] in ("overdue", "soon") and s["due_by"] == "km"


# ------------------------------------------------------------ the whole plan and the chart
def test_vehicle_plan_counts_and_sorts_worst_first():
    records = [rec("engine_oil", "2026-06-01"), rec("engine_filter", "2026-10-20")]
    plan = sp.vehicle_plan(TRUCK, group=S2, records=records, readings=[], overrides=[], today=TODAY)
    assert plan["items"][0]["key"] == "engine_oil" and plan["items"][0]["state"] == "overdue"
    assert plan["counts"]["overdue"] == 1 and plan["counts"]["ok"] == 1 and plan["counts"]["unknown"] == len(plan["items"]) - 2
    assert plan["worst"] == "overdue" and plan["has_plan"] and plan["group"]["code"] == "S2" and plan["group"]["months"] == 2
    assert plan["km_per_day"]["source"] == "default"


def test_vehicle_without_a_group_has_no_plan():
    plan = sp.vehicle_plan(TRUCK, group=None, records=[], readings=[], overrides=[], today=TODAY, with_chart=True)
    assert plan["items"] == [] and not plan["has_plan"] and plan["chart"] is None and plan["group"] is None


def test_a_truck_with_no_history_still_has_a_chart_full_of_question_marks():
    plan = sp.vehicle_plan(TRUCK, group=S2, records=[], readings=[], overrides=[], today=TODAY, with_chart=True)
    chart = plan["chart"]
    assert plan["counts"]["unknown"] == len(plan["items"]) and chart["no_history"] is True
    assert chart["columns"][0]["date"] == TODAY.isoformat() and "ไม่ทราบ" in chart["columns"][0]["label"]
    assert all(r["cells"][0] == "unknown" and r["last_date"] is None for r in chart["rows"])
    assert [i for i, c in enumerate(_row(chart, "engine_oil")["cells"]) if c == "due"] == list(range(1, sp.CHART_ROUNDS[sp.SMALL] + 1))
    assert [i for i, c in enumerate(_row(chart, "coolant")["cells"]) if c == "due"] == [4, 8]          # what to do when, from today
    with_km = sp.vehicle_plan(TRUCK, group=S2, records=[], readings=[{"kind": "odometer", "end_date": TODAY.isoformat(), "km": 120000}],
                              overrides=[], today=TODAY, with_chart=True)["chart"]
    assert with_km["columns"][0]["km"] == 120000 and with_km["columns"][1]["km"] == 130000


def _chart(group, records, today=TODAY, readings=None):
    return sp.vehicle_plan(TRUCK, group=group, records=records, readings=readings or [], overrides=[], today=today, with_chart=True)["chart"]


def _row(chart, key):
    return next(r for r in chart["rows"] if r["key"] == key)


def test_the_chart_columns_are_the_latest_event_then_the_next_rounds():
    chart = _chart(S2, [rec("engine_oil", "2026-10-22")])
    labels = [c["label"] for c in chart["columns"]]
    assert labels[:4] == ["ครั้งล่าสุด", "ครั้งถัดไป", "2 ครั้งถัดไป", "3 ครั้งถัดไป"] and len(labels) == 1 + sp.CHART_ROUNDS[sp.SMALL]
    assert [c["date"] for c in chart["columns"][:4]] == ["2026-10-22", "2026-12-22", "2027-02-22", "2027-04-22"]
    assert all(c["km"] is None for c in chart["columns"])                  # no odometer anywhere: only the order of the rounds


def test_a_full_service_follows_the_isuzu_pattern():
    """Everything done on the same day on a small truck: oil + engine filter every round, the 40k group every 4th."""
    done = [rec(k, "2026-10-22", odo=300_000) for k in sp.DEFAULT_KM[sp.SMALL]]
    chart = _chart(S2, done)
    assert _row(chart, "engine_oil")["cells"][0] == "done"
    assert [c for c in _row(chart, "engine_oil")["cells"][1:]] == ["due"] * sp.CHART_ROUNDS[sp.SMALL]
    assert [i for i, c in enumerate(_row(chart, "coolant")["cells"]) if c == "due"] == [4, 8]            # 40,000 km = 4 rounds
    assert [i for i, c in enumerate(_row(chart, "wheel_grease")["cells"]) if c == "due"] == [3, 6, 9]    # 30,000 km
    assert [i for i, c in enumerate(_row(chart, "injector_washers")["cells"]) if c == "due"] == [10]     # 100,000 km
    assert [c["km"] for c in chart["columns"][:3]] == [300_000, 310_000, 320_000]                         # km appear once known


def test_big_truck_rounds_are_20000_km_apart():
    done = [rec(k, "2026-10-22") for k in sp.DEFAULT_KM[sp.BIG]]
    chart = _chart(B5, done)
    assert len(chart["columns"]) == 1 + sp.CHART_ROUNDS[sp.BIG]
    assert [i for i, c in enumerate(_row(chart, "gearbox_oil")["cells"]) if c == "due"] == [2, 4, 6, 8]
    assert [i for i, c in enumerate(_row(chart, "air_filter")["cells"]) if c == "due"] == [4, 8]


def test_items_never_done_are_marked_unknown_in_the_next_round():
    chart = _chart(S2, [rec("engine_oil", "2026-10-22")])
    assert _row(chart, "gearbox_oil")["cells"][1] == "unknown" and _row(chart, "gearbox_oil")["last_date"] is None
    assert _row(chart, "engine_oil")["cells"][0] == "done"


def test_an_item_done_before_the_latest_event_falls_due_in_the_right_round():
    # gearbox done 2 rounds (4 months) before the latest oil change: 8 months interval -> due 4 months after it = round 2
    chart = _chart(S2, [rec("engine_oil", "2026-10-22"), rec("gearbox_oil", "2026-06-22")])
    row = _row(chart, "gearbox_oil")
    assert row["cells"][0] is None and row["last_date"] == "2026-06-22"
    assert [i for i, c in enumerate(row["cells"]) if c == "due"] == [2, 6, 10]                          # every 8 months = every 4 rounds


def test_an_item_already_past_due_shows_overdue_in_the_next_round():
    chart = _chart(S2, [rec("engine_oil", "2026-10-22"), rec("coolant", "2026-01-01")])
    row = _row(chart, "coolant")
    assert row["cells"][1] == "overdue" and row["state"] == "overdue"
    assert [i for i, c in enumerate(row["cells"]) if c == "due"] == [4, 8]                               # then every 8 months after it was due


def test_things_done_within_a_few_days_count_as_the_same_event():
    chart = _chart(S2, [rec("engine_oil", "2026-10-22"), rec("engine_filter", "2026-10-20")])
    assert _row(chart, "engine_filter")["cells"][0] == "done"


def test_recording_a_new_event_moves_the_chart_on():
    before = _chart(S2, [rec("engine_oil", "2026-08-22")], today=date(2026, 10, 22))
    after = _chart(S2, [rec("engine_oil", "2026-08-22"), rec("engine_oil", "2026-10-22"), rec("gearbox_oil", "2026-10-22")])
    assert before["columns"][0]["date"] == "2026-08-22" and after["columns"][0]["date"] == "2026-10-22"
    assert _row(after, "gearbox_oil")["cells"][0] == "done" and _row(before, "gearbox_oil")["cells"][0] is None


def test_kk1_uses_its_fractional_months():
    chart = _chart(KK1, [rec("engine_oil", "2026-10-01")])
    assert [c["date"] for c in chart["columns"][:3]] == ["2026-10-01", "2026-12-22", "2027-03-13"]


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


def test_clean_interval_changes_km_only():
    row = sp.clean_interval({"vehicle_class": sp.BIG, "item_key": "engine_oil", "interval_km": "15000"})
    assert row == {"vehicle_class": sp.BIG, "item_key": "engine_oil", "interval_km": 15000, "interval_months": None}
    for bad in ({"vehicle_class": sp.BIG, "item_key": "engine_oil", "interval_km": ""},
                {"vehicle_class": sp.BIG, "item_key": "injector_washers", "interval_km": 1000},       # big trucks have no such item
                {"vehicle_class": "รถบัส", "item_key": "engine_oil", "interval_km": 1000}):
        with pytest.raises(sp.InvalidPlan):
            sp.clean_interval(bad)


def test_clean_group():
    assert sp.clean_group({"code": "S2", "months_per_step": "2.5"}, known=GROUPS) == {"code": "S2", "months_per_step": 2.5, "km_per_day": None}
    assert sp.clean_group({"code": "KK1", "km_per_day": "250"}, known=GROUPS)["km_per_day"] == 250
    for bad in ({"code": "ZZ", "months_per_step": 2}, {"code": "S2"}, {"code": "S2", "months_per_step": "0"}, {"code": "S2", "months_per_step": "abc"},
                {"code": "S2", "months_per_step": "99"}):
        with pytest.raises(sp.InvalidPlan):
            sp.clean_group(bad, known=GROUPS)


def test_clean_vehicle_group():
    assert sp.clean_vehicle_group({"vehicle_id": VID, "group_code": "B5"}, known=GROUPS) == (VID, "B5")
    assert sp.clean_vehicle_group({"vehicle_id": VID, "group_code": ""}, known=GROUPS) == (VID, None)        # no schedule
    with pytest.raises(sp.InvalidPlan):
        sp.clean_vehicle_group({"vehicle_id": VID, "group_code": "ZZ"}, known=GROUPS)
    with pytest.raises(sp.InvalidPlan):
        sp.clean_vehicle_group({"vehicle_id": "nope", "group_code": "S2"}, known=GROUPS)


# ------------------------------------------------- the settings page: groups and which truck follows which
def test_a_group_is_customised_only_when_its_numbers_differ_from_the_built_in_ones():
    s2 = next(g for g in sp.DEFAULT_GROUPS if g["code"] == "S2")
    assert not sp.group_customised({**s2, "months_per_step": "2"})          # the database gives numbers as text
    assert sp.group_customised({**s2, "months_per_step": 3})
    assert all("เดือน ต่อ" not in g["label"] for g in sp.DEFAULT_GROUPS)    # the numbers are shown beside the name, not in it


def test_several_trucks_can_change_group_in_one_save():
    known = sp.merge_groups([])
    a, b = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    out = sp.clean_vehicle_groups({"changes": [{"vehicle_id": a, "group_code": "S3"}, {"vehicle_id": b, "group_code": ""}]}, known=known)
    assert out == [(a, "S3"), (b, None)]
    for bad in ({"changes": []}, {"changes": [{"vehicle_id": a, "group_code": "ZZ"}]}, {}):
        with pytest.raises(sp.InvalidPlan):
            sp.clean_vehicle_groups(bad, known=known)


def test_every_item_is_reminded_about_unless_changed():
    assert sp.DEFAULT_REMIND_ITEMS is None


def test_a_bill_that_says_only_inject_grease_counts_as_the_wheel_grease_service():
    for line in ("อัดจารบี", "อัดจาระบี", "ค่าอัดจารบี", "อัด จารบี ช่วงล่าง", "จาระบีลูกปืนล้อ"):
        assert sp.classify_line(line) == "wheel_grease", line
    assert sp.classify_line("จารบี 1 กระป๋อง") is None                  # a part, not the service


def test_the_groups_say_what_their_oil_round_is(monkeypatch):
    monkeypatch.setattr(sp, "has_v10", lambda: True)
    monkeypatch.setattr(sp.supabase, "select_all", lambda table, params: [{"vehicle_class": sp.SMALL, "item_key": "engine_oil", "interval_km": 20_000, "interval_months": None}]
                        if table == "service_intervals" else [])
    monkeypatch.setattr(sp, "_vehicles", lambda *a, **k: [])
    groups = {g["code"]: g for g in sp.settings()["groups"]}
    assert groups["S2"]["oil_km"] == 20_000 and groups["S2"]["oil_months"] == pytest.approx(4)       # changed: 2 months per 10,000 km -> 4 months
    assert groups["B5"]["oil_km"] == 20_000 and groups["B5"]["oil_months"] == pytest.approx(5)       # untouched


# ------------------------------------------------- the calendar items and the other new default items
def test_a_flush_and_a_chassis_check_are_counted_in_months_for_every_truck():
    assert sp.FIXED_MONTHS == {"engine_flush": 12, "chassis_check": 3}
    for group in (S2, S3, B5, B6, KK1):                  # whatever the group's driving rate, the same months
        plan = {i["key"]: i for i in sp.effective_plan(group)}
        assert plan["engine_flush"]["interval_months"] == 12 and plan["engine_flush"]["interval_km"] is None
        assert plan["chassis_check"]["interval_months"] == 3 and plan["chassis_check"]["interval_km"] is None


def test_a_calendar_item_falls_due_by_the_calendar():
    plan = sp.vehicle_plan(TRUCK, group=B5, records=[rec("engine_flush", "2026-01-10"), rec("chassis_check", "2026-09-10")], readings=[], overrides=[], today=TODAY)
    items = {i["key"]: i for i in plan["items"]}
    assert items["engine_flush"]["due_date"] == "2027-01-10" and items["engine_flush"]["due_by"] == "months" and items["engine_flush"]["km_left"] is None
    assert items["chassis_check"]["due_date"] == "2026-12-10"


def test_the_intake_manifold_is_for_the_small_trucks_at_100000_km():
    assert sp.DEFAULT_KM[sp.SMALL]["intake_manifold"] == 100_000 and "intake_manifold" not in sp.DEFAULT_KM[sp.BIG]
    assert sp.effective_plan(S2)[[i["key"] for i in sp.effective_plan(S2)].index("intake_manifold")]["interval_months"] == pytest.approx(20)


def test_the_km_of_a_calendar_item_cannot_be_set_in_the_settings():
    with pytest.raises(sp.InvalidPlan):
        sp.clean_interval({"vehicle_class": sp.SMALL, "item_key": "engine_flush", "interval_km": "1000"})


@pytest.mark.parametrize("line,key", [
    ("ฟลัชชิ่งน้ำมันเครื่อง", "engine_flush"), ("flushing เครื่อง", "engine_flush"), ("ล้างภายในเครื่อง", "engine_flush"),
    ("เช็คช่วงล่าง", "chassis_check"), ("เปลี่ยนโช้คอัพหน้า", "chassis_check"), ("เสริมแหนบ", "chassis_check"), ("สลักคอม้า", "chassis_check"),
    ("ล้างท่อร่วมไอดี", "intake_manifold"), ("ค่าล้างท่อไอดี", "intake_manifold"), ("ทำความสะอาดท่อร่วมไอดี", "intake_manifold"),
    ("ล้างทำความสะอาดท่อไอดี", "intake_manifold"), ("ท่อร่วมไอดี", "intake_manifold"), ("เปลี่ยนท่อร่วมไอดี", "intake_manifold"),
    ("น้ำมันเครื่อง", "engine_oil"),                           # the older rules still win where they match
])
def test_bill_lines_for_the_new_items(line, key):
    assert sp.classify_line(line) == key


def test_the_parts_are_not_the_services():
    assert sp.classify_line("ประเก็นท่อร่วมไอดี") is None                         # a gasket, not the manifold
    assert sp.classify_line("ท่อไอดี") is None and sp.classify_line("เปลี่ยนท่อไอดี") is None   # the intake hose is another part
