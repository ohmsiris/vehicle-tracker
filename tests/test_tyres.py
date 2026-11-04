import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tyres as ty

TODAY = date(2026, 11, 5)


def test_sizes_are_written_one_way():
    assert ty.norm_size("ยางเรเดียล 225/75R14 MICHELIN XCD2") == "225/75R14"
    assert ty.norm_size("205/70 R15") == "205/70R15"
    assert ty.norm_size("225/75-15") == "225/75R15"
    assert ty.norm_size("ยางใน") == ""


def test_tyre_lines_are_told_apart_from_rubber_parts():
    assert ty.is_tyre_line("ยางเรเดียล 225/75R14 MICHELIN XCD2")
    assert ty.is_tyre_line("ยางเรเดียล 205/70R15 BRIDGESTONE DURAVIS R611")
    for not_tyre in ("ยางกันฝุ่นเบรคหน้า L", "ยางแท่นเครื่อง L-R", "ลูกยางเบรคหน้า L", "ท่อยางหม้อน้ำบน-ล่าง", "ยางฝาครอบวาล์ว", "ยางใน 7.50-16", "ปะยาง"):
        assert not ty.is_tyre_line(not_tyre), not_tyre
    assert ty.brand_of("ยางเรเดียล 205/70R15 BRIDGESTONE DURAVIS R611") == "Bridgestone"


def test_six_wheels_for_big_trucks_four_otherwise():
    assert ty.default_wheels({"vehicle_class": "รถใหญ่"}) == 6
    assert ty.default_wheels({"vehicle_class": "รถเล็ก"}) == 4
    assert ty.positions(6) == ("FL", "FR", "RLO", "RLI", "RRI", "RRO")


def test_change_needs_a_wheel_and_a_date():
    with pytest.raises(ty.InvalidTyre):
        ty.clean_change({"fitted_on": "2026-10-27"}, 4, TODAY)
    with pytest.raises(ty.InvalidTyre):
        ty.clean_change({"positions": ["RL"]}, 4, TODAY)
    with pytest.raises(ty.InvalidTyre):
        ty.clean_change({"positions": ["RLO"], "fitted_on": "2026-10-27"}, 4, TODAY)     # a 6-wheel position on a 4-wheel truck
    with pytest.raises(ty.InvalidTyre):
        ty.clean_change({"positions": ["RL"], "fitted_on": "2569-10-27"}, 4, TODAY)       # a พ.ศ. year
    picked, t = ty.clean_change({"positions": ["RL", "RR"], "unknown_count": "1", "fitted_on": "2026-10-27", "fitted_km": "424,683",
                                 "size": "225/75 R14", "price": "4800"}, 4, TODAY)
    assert picked == ["RL", "RR", "?"] and t["fitted_km"] == 424683 and t["size"] == "225/75R14" and t["price"] == 4800


def test_rotation_must_not_put_two_tyres_on_one_wheel():
    current = {"FL": {"id": "a"}, "FR": {"id": "b"}, "RL": {"id": "c"}, "RR": {"id": "d"}}
    assert ty.clean_rotation({"FL": "RL", "RL": "FL"}, current, 4) == {"FL": "RL", "RL": "FL"}
    with pytest.raises(ty.InvalidTyre):
        ty.clean_rotation({"FL": "RL"}, current, 4)                 # RL's tyre would stay where FL's lands
    with pytest.raises(ty.InvalidTyre):
        ty.clean_rotation({"FL": "FL"}, current, 4)


def test_wheel_view_ages_and_history():
    rows = [{"id": "1", "position": "RL", "fitted_on": "2024-09-01", "fitted_km": 400000, "removed_on": "2026-10-27"},
            {"id": "2", "position": "RL", "fitted_on": "2026-10-27", "fitted_km": 424683, "removed_on": None},
            {"id": "3", "position": "FL", "fitted_on": "2024-06-01", "removed_on": None},
            {"id": "4", "position": "?", "fitted_on": "2023-01-01", "removed_on": None}]
    v = ty.wheel_view({"wheels": 4, "front_size": "205/70R15"}, {"vehicle_class": "รถเล็ก"}, rows, TODAY, 425000)
    by = {w["position"]: w for w in v["positions"]}
    assert by["RL"]["tyre"]["id"] == "2" and by["RL"]["state"] == "ok" and by["RL"]["km_used"] == 317 and [h["id"] for h in by["RL"]["history"]] == ["1"]
    assert by["FL"]["state"] == "old" and by["FL"]["months"] == 29
    assert by["FR"]["state"] == "none"
    assert [u["id"] for u in v["unknown"]] == ["4"]


def test_bill_tyres_lists_unplaced_tyre_lines():
    events = [{"id": "e1", "event_date": "2026-10-27", "mileage": 424683, "vendor": "สระบุรี เซอร์วิส",
               "bill_line_items": [{"description": "ยางเรเดียล 225/75R14 MICHELIN XCD2", "quantity": 2, "unit_cost": 4800, "line_total": 9600}]},
              {"id": "e2", "event_date": "2026-10-02", "bill_line_items": [{"description": "ยางกันฝุ่นเบรคหน้า L", "quantity": 1, "line_total": 100}]},
              {"id": "e3", "event_date": "2026-10-16", "bill_line_items": [{"description": "ยางเรเดียล 205/70R15 BRIDGESTONE", "quantity": 2, "line_total": 6400}]}]
    out = ty.bill_tyres(events, placed_events={"e3"})
    assert len(out) == 1 and out[0]["size"] == "225/75R14" and out[0]["brand"] == "Michelin" and out[0]["qty"] == 2 and out[0]["mileage"] == 424683
