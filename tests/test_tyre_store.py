import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tyre_store as ts
from app.tyres import InvalidTyre

TODAY = date(2026, 11, 5)
SIX_SPEC = {"front_size": "7.50R16", "rear_size": "7.50R16", "wheels": 6}


def tyre(i, pos, size="215/75R17", brand="Bridgestone"):
    return {"id": f"t{i}", "position": pos, "size": size, "brand": brand, "notes": None}


def six_current():
    return {p: tyre(i, p) for i, p in enumerate(("FL", "FR", "RLO", "RLI", "RRI", "RRO"))}


def test_the_usual_six_wheeler_job_fronts_to_rear_inner_and_new_fronts():
    cur = six_current()
    data = {"done_on": "2026-11-01", "km": 250_000,
            "wheels": {"RLI": {"action": "move", "from": "FL"}, "RRI": {"action": "move", "from": "FR"},
                       "FL": {"action": "new", "size": "215/75R17", "brand": "Michelin", "price": "5200"}, "FR": {"action": "new", "size": "215/75R17"}},
            "removed": {"t3": {"reason": "worn", "to": "scrap"}, "t4": {"reason": "worn", "to": "spare"}}}
    job = ts.clean_job(data, cur, 6, None, TODAY)
    assert sorted((t["id"], dst) for t, dst in job["moves"]) == [("t0", "RLI"), ("t1", "RRI")]
    assert sorted((t["id"], r["to"]) for t, r in job["removed"]) == [("t3", "scrap"), ("t4", "spare")]     # the old inner rears came off
    assert [a["position"] for a in job["added"]] == ["FL", "FR"] and job["added"][0]["price"] == 5200


def test_a_tyre_left_without_a_wheel_must_say_why_and_where():
    cur = six_current()
    data = {"done_on": "2026-11-01", "wheels": {"RLI": {"action": "move", "from": "FL"}, "FL": {"action": "new", "size": "215/75R17"}}}
    with pytest.raises(InvalidTyre):
        ts.clean_job(data, cur, 6, None, TODAY)                      # the old RLI (t3) is not accounted for
    data["removed"] = {"t3": {"reason": "burst"}}
    with pytest.raises(InvalidTyre):
        ts.clean_job(data, cur, 6, None, TODAY)                      # no destination
    data["removed"] = {"t3": {"reason": "burst", "to": "scrap"}}
    assert len(ts.clean_job(data, cur, 6, None, TODAY)["removed"]) == 1


def test_a_spare_from_the_store_needs_a_size_and_a_tyre_cannot_be_in_two_places():
    cur = {"FL": tyre(1, "FL", "205/70R15"), "RL": tyre(2, "RL", "225/75R14")}
    with pytest.raises(InvalidTyre):
        ts.clean_job({"done_on": "2026-11-01", "wheels": {"RR": {"action": "spare"}}}, cur, 4, None, TODAY)
    job = ts.clean_job({"done_on": "2026-11-01", "wheels": {"RR": {"action": "spare", "size": "225/75 R14"}}}, cur, 4, None, TODAY)
    assert job["added"][0]["source"] == "spare" and job["added"][0]["size"] == "225/75R14"
    with pytest.raises(InvalidTyre):                                  # RL kept in place AND moved to RR
        ts.clean_job({"done_on": "2026-11-01", "wheels": {"RR": {"action": "move", "from": "RL"}}}, cur, 4, None, TODAY)


def test_sizes_stay_on_their_axle_in_a_job():
    spec = {"front_size": "205/70R15", "rear_size": "225/75R14"}
    cur = {"FL": tyre(1, "FL", "205/70R15"), "RL": tyre(2, "RL", "225/75R14")}
    with pytest.raises(InvalidTyre):
        ts.clean_job({"done_on": "2026-11-01", "wheels": {"FR": {"action": "move", "from": "RL"}, "RL": {"action": "new"}}}, cur, 4, spec, TODAY)
    with pytest.raises(InvalidTyre):
        ts.clean_job({"done_on": "2026-11-01", "wheels": {"FR": {"action": "spare", "size": "225/75R14"}}}, cur, 4, spec, TODAY)


def test_a_count_returns_what_is_missing_or_extra():
    assert ts.count_diffs({"225/75R14": 1, "205/70R15": 3, "7.50R16": 2}, {"225/75R14": 2, "205/70R15": 3}) == [
        {"size": "225/75R14", "counted": 1, "expected": 2, "diff": -1}, {"size": "7.50R16", "counted": 2, "expected": 0, "diff": 2}]


def test_store_sizes_include_big_truck_bias_sizes():
    assert ts.store_size("ยาง 7.50-16 14PR") == "7.50-16" and ts.store_size("7.50R16") == "7.50-16" and ts.store_size("225/75 R14") == "225/75R14"
    assert ts.store_size("215/75R17.5") == "215/75R17.5" and ts.store_size("ยางใน") == ""
