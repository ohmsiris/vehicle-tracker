import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import machine_plan as mp
from app import machinery as mc

TODAY = date(2026, 10, 30)
M = {"id": "m1", "name": "เครื่อง 1", "kind": "เครื่อง", "number": 1}
SONG = {"id": "m6", "name": "ซอง", "kind": "บ่อซอง"}


def ev(key, day, **kw):
    return {"id": f"e-{key}-{day}", "machine_id": "m1", "job_key": key, "event_date": day, "created_at": day, **kw}


def status(key, events, machine=M, readings=(), settings=None, today=TODAY):
    plan = mp.plan_for_machine(machine, events, list(readings), settings or {}, today)
    return next(j for j in plan["jobs"] if j["key"] == key), plan


# ----------------------------------------------------------------- dates and rates
def test_add_months_keeps_the_day_or_the_end_of_a_short_month():
    assert mp.add_months(date(2026, 3, 4), 3) == date(2026, 6, 4)
    assert mp.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert mp.add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_running_rate_is_assumed_until_two_readings_a_week_apart_exist():
    assert mp.machine_rate(M, [])["per_day"] == 24
    assert mp.machine_rate(SONG, [])["per_day"] == pytest.approx(24 * 6 / 7)
    assert mp.machine_rate({**M, "hours_per_day": 20, "days_per_week": 6}, [])["per_day"] == pytest.approx(20 * 6 / 7)
    one_week = [{"read_on": "2026-10-01", "hours": 1000}, {"read_on": "2026-10-04", "hours": 1072}]
    assert mp.machine_rate(M, one_week)["source"] == "สมมติ"                      # only 3 days apart
    real = [{"read_on": "2026-10-01", "hours": 1000}, {"read_on": "2026-10-11", "hours": 1200}]
    r = mp.machine_rate(M, real)
    assert r["per_day"] == 20 and r["source"] == "มิเตอร์"


def test_hours_now_is_the_last_reading_plus_the_running_since():
    readings = [{"read_on": "2026-10-20", "hours": 5000}, {"read_on": "2026-10-10", "hours": 4800}]
    now = mp.hours_now(readings, 20, TODAY)
    assert now["hours"] == 5200 and now["estimated"] and now["read_on"] == "2026-10-20"
    assert mp.hours_now([], 20, TODAY) is None


# ------------------------------------------------------------------- job states
def test_a_month_job_is_ok_soon_or_late():
    # cooling wash every 3 months
    assert status("cooling", [ev("cooling", "2026-09-30")])[0]["state"] == "ok"
    soon = status("cooling", [ev("cooling", "2026-08-05")])[0]
    assert soon["state"] == "soon" and soon["due_date"] == "2026-11-05" and soon["days_left"] == 6
    late = status("cooling", [ev("cooling", "2026-03-04")])[0]
    assert late["state"] == "late" and late["due_date"] == "2026-06-04" and late["days_left"] == -148


def test_no_report_is_none_and_a_job_never_done_is_never():
    assert status("cooling", [])[0]["state"] == "none"
    assert status("overhaul", [])[0]["state"] == "none"
    never = status("overhaul", [], machine={**M, "never_overhauled": True})[0]
    assert never["state"] == "never"
    # never overhauled + a meter: the whole meter counts
    r = [{"read_on": "2026-10-30", "hours": 31000}]
    n = status("overhaul", [], machine={**M, "never_overhauled": True}, readings=r)[0]
    assert n["state"] == "never" and n["hours_since"] == 31000


def test_the_latest_report_wins_and_an_overhaul_restarts_the_oil_clock():
    events = [ev("oil", "2026-01-22"), ev("oil", "2026-06-18")]
    assert status("oil", events)[0]["last_date"] == "2026-06-18"
    # an overhaul after the last oil change counts as an oil change (the oil is replaced at overhaul)
    j = status("oil", [ev("oil", "2026-01-22"), ev("overhaul", "2026-06-11")])[0]
    assert j["last_date"] == "2026-06-11" and j["last_job"] == "overhaul"


def test_an_hours_job_runs_off_the_assumed_pattern_without_a_meter():
    # oil: 4000 h or 6 months. Done 2026-06-17 at 24 h x 7 days: 4000 h last 166 days (2026-11-30), before the 6 months (2026-12-17)
    j = status("oil", [ev("oil", "2026-06-17")])[0]
    assert j["hours_estimated"] and j["hours_since"] == (TODAY - date(2026, 6, 17)).days * 24
    assert j["due_date"] == "2026-11-30" and j["state"] == "ok"


def test_an_exact_meter_reading_beats_the_estimate():
    events = [ev("grease", "2026-06-17", hours_at=10000)]                       # grease is hours only (4000 h)
    readings = [{"read_on": "2026-10-30", "hours": 12000}]
    j = status("grease", events, readings=readings)[0]
    assert j["hours_since"] == 2000 and not j["hours_estimated"]
    assert j["days_left"] == 83                    # 2000 h left at 24 h a day


def test_the_brine_pond_machines_run_six_days_a_week():
    j = status("overhaul", [ev("overhaul", "2025-10-30")], machine=SONG)[0]
    assert j["hours_since"] == round(365 * 24 * 6 / 7)
    assert j["hours"] == 24000 and j["state"] == "ok"


def test_whichever_comes_first_months_or_hours():
    # safety is months only; oil has both: a machine at 12 h/day reaches 6 months before 4000 h
    m = {**M, "hours_per_day": 12}
    j = status("oil", [ev("oil", "2026-04-30")], machine=m)[0]
    assert j["due_date"] == "2026-10-30" and j["state"] == "soon" and j["days_left"] == 0


# --------------------------------------------------------------- which jobs apply
def test_freezer_jobs_only_for_the_numbered_machines():
    keys = {j["key"] for j in mp.jobs_for(M, {})}
    song = {j["key"] for j in mp.jobs_for(SONG, {})}
    assert {"freezer_wash", "freezer_vac", "screw_grease"} <= keys
    assert not ({"freezer_wash", "freezer_vac", "screw_grease", "screw_overhaul"} & song)
    assert "overhaul" in song and "cooling" in song


def test_the_owner_can_change_an_interval_or_switch_a_job_off():
    s = {"cooling": {"job_key": "cooling", "interval_months": 2, "interval_hours": None, "level": None},
         "safety": {"job_key": "safety", "interval_months": None, "interval_hours": None, "level": "ไม่ต้องทำ"}}
    jobs = {j["key"]: j for j in mp.jobs_for(M, s)}
    assert jobs["cooling"]["months"] == 2 and "safety" not in jobs
    assert status("cooling", [ev("cooling", "2026-09-05")], settings=s)[0]["due_date"] == "2026-11-05"
    # an interval of 0 means "no interval of this kind"
    off = {"oil": {"job_key": "oil", "interval_months": 0, "interval_hours": 3000, "level": None}}
    j = {x["key"]: x for x in mp.jobs_for(M, off)}["oil"]
    assert j["months"] is None and j["hours"] == 3000


def test_overview_ordering_puts_the_worst_first_and_counts_them():
    events = [ev("cooling", "2026-03-04"), ev("oil", "2026-10-01"), ev("condenser", "2026-03-31")]
    plan = mp.plan_for_machine({**M, "never_overhauled": True}, events, [], {}, TODAY)
    assert plan["jobs"][0]["key"] == "overhaul" and plan["jobs"][0]["state"] == "never"
    assert plan["counts"]["late"] >= 3                                   # overhaul (never) + cooling + condenser
    assert plan["next"]["key"] == "overhaul"
    assert [j["state"] for j in plan["jobs"]] == sorted((j["state"] for j in plan["jobs"]), key=lambda s: mp.STATE_RANK[s])


# ------------------------------------------------------------ settings validation
def test_a_job_setting_is_checked():
    assert mp.clean_setting("cooling", {"interval_months": "2"}) == {"interval_months": 2}
    assert mp.clean_setting("cooling", {"level": "ไม่ต้องทำ"}) == {"level": "ไม่ต้องทำ"}
    with pytest.raises(mp.NotFound):
        mp.clean_setting("nope", {"level": "ทำเอง"})
    with pytest.raises(mc.InvalidMachinery):
        mp.clean_setting("cooling", {"level": "บางที"})
    with pytest.raises(mc.InvalidMachinery):
        mp.clean_setting("cooling", {"interval_months": "x"})
    with pytest.raises(mc.InvalidMachinery):
        mp.clean_setting("cooling", {"interval_months": 0})                # a job needs some interval
    with pytest.raises(mc.InvalidMachinery):
        mp.clean_setting("cooling", {})
    assert mp.clean_setting("oil", {"interval_months": 0})                  # oil still has its hours


# ------------------------------------------------- machinery.py additions (v33)
MACHINES = {"11111111-1111-1111-1111-111111111111": {"id": "11111111-1111-1111-1111-111111111111", "branch": "สระบุรี", "name": "เครื่อง 1", "kind": "เครื่อง", "number": 1},
            "22222222-2222-2222-2222-222222222222": {"id": "22222222-2222-2222-2222-222222222222", "branch": "สระบุรี", "name": "เครื่อง 2", "kind": "เครื่อง", "number": 2}}
A, B = list(MACHINES)


def test_a_report_can_carry_its_job_and_meter_reading():
    v = mc.clean_event({"event_date": "2026-10-01", "summary": "ถ่ายน้ำมัน", "machine_id": A, "job_key": "oil", "hours_at": "12,000"}, machines=MACHINES, today=TODAY)
    assert v["job_key"] == "oil" and v["hours_at"] == 12000
    with pytest.raises(mc.InvalidMachinery):
        mc.clean_event({"event_date": "2026-10-01", "summary": "x", "machine_id": A, "hours_at": "abc"}, machines=MACHINES, today=TODAY)


def test_a_part_can_be_shared_with_other_machines_but_not_with_its_own():
    v = mc.clean_part({"location": "เครื่อง 1", "equipment": "บดเครื่อง 1/2", "machine_id": A, "shared_machine_ids": [A, B, B]}, machine_ids=set(MACHINES))
    assert v["shared_machine_ids"] == [B]
    with pytest.raises(mc.InvalidMachinery):
        mc.clean_part({"location": "x", "equipment": "บด", "shared_machine_ids": ["33333333-3333-3333-3333-333333333333"]}, machine_ids=set(MACHINES))


def test_a_shared_part_shows_up_on_each_machine_it_serves():
    grinder = {"id": "p", "location": "เครื่อง 1", "equipment": "บดเครื่อง 1/2", "machine_id": A, "shared_machine_ids": [B]}
    other = {"id": "q", "location": "เครื่อง 1", "equipment": "ปั๊ม", "machine_id": A, "shared_machine_ids": []}
    assert mc.filter_parts([grinder, other], MACHINES, machine_id=B) == [grinder]
    assert mc.filter_parts([grinder, other], MACHINES, machine_id=A) == [grinder, other]


def test_machine_running_pattern_and_never_overhauled_are_validated():
    v = mc.clean_machine({"hours_per_day": "20", "days_per_week": "6", "never_overhauled": True}, existing={"id": "x", "branch": "ส", "kind": "เครื่อง", "number": 1, "name": "เครื่อง 1"}, others=[])
    assert v["hours_per_day"] == 20 and v["days_per_week"] == 6 and v["never_overhauled"] is True
    for bad in ({"hours_per_day": 30}, {"days_per_week": 0}, {"days_per_week": 9}):
        with pytest.raises(mc.InvalidMachinery):
            mc.clean_machine(bad, existing={"id": "x", "branch": "ส", "kind": "เครื่อง", "number": 1, "name": "เครื่อง 1"}, others=[])
