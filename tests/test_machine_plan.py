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
PROGRAMS = mp.merge_programs([])


def ev(key, day, **kw):
    return {"id": f"e-{key}-{day}", "machine_id": "m1", "job_key": key, "event_date": day, "created_at": day, **kw}


def plan(machine=M, events=(), readings=(), programs=None, today=TODAY, chart=False):
    programs = programs or PROGRAMS
    return mp.plan_for_machine(machine, programs[mp.program_code_for(machine, programs)], list(events), list(readings), today, with_chart=chart)


def status(key, events, machine=M, readings=(), programs=None, today=TODAY):
    p = plan(machine, events, readings, programs, today)
    return next(j for j in p["jobs"] if j["key"] == key), p


# ----------------------------------------------------------------- dates and rates
def test_add_months_keeps_the_day_or_the_end_of_a_short_month():
    assert mp.add_months(date(2026, 3, 4), 3) == date(2026, 6, 4)
    assert mp.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert mp.add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_running_rate_is_assumed_until_two_readings_a_week_apart_exist():
    assert mp.machine_rate(M, [])["per_day"] == 24
    assert mp.machine_rate(SONG, [])["per_day"] == pytest.approx(24 * 6 / 7)
    assert mp.machine_rate({**M, "hours_per_day": 20, "days_per_week": 6}, [])["per_day"] == pytest.approx(20 * 6 / 7)
    close = [{"read_on": "2026-10-01", "hours": 1000}, {"read_on": "2026-10-04", "hours": 1072}]
    assert mp.machine_rate(M, close)["source"] == "สมมติ"                      # only 3 days apart
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
    assert status("cooling", [ev("cooling", "2026-09-30")])[0]["state"] == "ok"
    soon = status("cooling", [ev("cooling", "2026-08-05")])[0]
    assert soon["state"] == "soon" and soon["due_date"] == "2026-11-05" and soon["days_left"] == 6
    late = status("cooling", [ev("cooling", "2026-03-04")])[0]
    assert late["state"] == "late" and late["due_date"] == "2026-06-04" and late["days_left"] == -148


def test_no_report_is_none_and_a_job_never_done_is_never():
    assert status("cooling", [])[0]["state"] == "none"
    assert status("overhaul", [])[0]["state"] == "none"
    assert status("overhaul", [], machine={**M, "never_overhauled": True})[0]["state"] == "never"
    r = [{"read_on": "2026-10-30", "hours": 31000}]                         # never overhauled + a meter: the whole meter counts
    n = status("overhaul", [], machine={**M, "never_overhauled": True}, readings=r)[0]
    assert n["state"] == "never" and n["hours_since"] == 31000


def test_the_latest_report_wins_and_an_overhaul_restarts_the_oil_clock():
    assert status("oil", [ev("oil", "2026-01-22"), ev("oil", "2026-06-18")])[0]["last_date"] == "2026-06-18"
    j = status("oil", [ev("oil", "2026-01-22"), ev("overhaul", "2026-06-11")])[0]
    assert j["last_date"] == "2026-06-11" and j["last_job"] == "overhaul"


def test_an_hours_job_runs_off_the_assumed_pattern_without_a_meter():
    # oil: 4000 h or 6 months. Done 2026-06-17 at 24 h x 7 days: 4000 h last 166 days (2026-11-30), before the 6 months (2026-12-17)
    j = status("oil", [ev("oil", "2026-06-17")])[0]
    assert j["hours_estimated"] and j["hours_since"] == (TODAY - date(2026, 6, 17)).days * 24
    assert j["due_date"] == "2026-11-30" and j["state"] == "ok"


def test_an_exact_meter_reading_beats_the_estimate():
    events = [ev("grease", "2026-06-17", hours_at=10000)]                       # grease is hours only (4000 h)
    j = status("grease", events, readings=[{"read_on": "2026-10-30", "hours": 12000}])[0]
    assert j["hours_since"] == 2000 and not j["hours_estimated"]
    assert j["days_left"] == 83                                                 # 2000 h left at 24 h a day


def test_the_brine_pond_machines_run_six_days_a_week():
    j = status("overhaul", [ev("overhaul", "2025-10-30")], machine=SONG)[0]
    assert j["hours_since"] == round(365 * 24 * 6 / 7) and j["state"] == "ok"


def test_whichever_comes_first_months_or_hours():
    j = status("oil", [ev("oil", "2026-04-30")], machine={**M, "hours_per_day": 12})[0]      # 12 h a day: the 6 months come first
    assert j["due_date"] == "2026-10-30" and j["state"] == "soon" and j["days_left"] == 0


def test_worst_first_and_counted():
    p = plan({**M, "never_overhauled": True}, [ev("cooling", "2026-03-04"), ev("oil", "2026-10-01"), ev("condenser", "2026-03-31")])
    assert p["jobs"][0]["key"] == "overhaul" and p["jobs"][0]["state"] == "never"
    assert p["counts"]["late"] == 3 and p["next"]["key"] == "overhaul"
    assert [j["state"] for j in p["jobs"]] == sorted((j["state"] for j in p["jobs"]), key=lambda s: mp.STATE_RANK[s])


# --------------------------------------------------------------------- programs
def test_each_kind_of_machine_gets_its_built_in_program():
    assert mp.program_code_for(M, PROGRAMS) == "TUBE" and mp.program_code_for(SONG, PROGRAMS) == "SONG"
    tube = {j["key"] for j in mp.jobs_for(PROGRAMS["TUBE"])}
    song = {j["key"] for j in mp.jobs_for(PROGRAMS["SONG"])}
    assert {"freezer_wash", "freezer_vac", "screw_grease", "screw_overhaul"} <= tube
    assert not ({"freezer_wash", "freezer_vac", "screw_grease", "screw_overhaul"} & song)          # no freezer on a brine pond
    assert {"overhaul", "oil", "cooling", "condenser"} <= song
    assert mp.program_code_for({**SONG, "program_code": "P9"}, PROGRAMS) == "SONG"      # a program that is gone: back to the kind's


def test_a_saved_program_is_laid_over_the_defaults():
    rows = [{"code": "TUBE", "label": "หลอด (ของเรา)", "step_months": 2,
             "jobs": {"cooling": {"months": 2}, "safety": {"on": False}, "headwash": {"level": "ไม่ต้องทำ"}}},
            {"code": "P1", "label": "แก่งคอย", "step_months": 3, "jobs": {"oil": {"on": True, "months": 6, "hours": 4000, "level": "ทำเอง"}}}]
    progs = mp.merge_programs(rows)
    tube = {j["key"]: j for j in mp.jobs_for(progs["TUBE"])}
    assert progs["TUBE"]["label"] == "หลอด (ของเรา)" and progs["TUBE"]["step_months"] == 2 and progs["TUBE"]["customised"]
    assert tube["cooling"]["months"] == 2 and tube["oil"]["months"] == 6
    assert "safety" not in tube and "headwash" not in tube                    # switched off / ไม่ต้องทำ
    assert [j["key"] for j in mp.jobs_for(progs["P1"])] == ["oil"]               # an added program has only what was switched on
    assert not progs["P1"]["builtin"] and progs["SONG"]["builtin"] and not progs["SONG"]["customised"]
    assert [j["key"] for j in plan({**M, "program_code": "P1"}, programs=progs)["jobs"]] == ["oil"]


def test_a_program_is_checked_before_it_is_saved():
    cur = PROGRAMS["TUBE"]
    v = mp.clean_program({"label": " หลอด ", "step_months": "2", "jobs": {"cooling": {"on": True, "months": "2", "hours": "", "level": "ทำเอง"}}}, cur)
    assert v == {"label": "หลอด", "step_months": 2, "jobs": {"cooling": {"on": True, "months": 2, "hours": None, "level": "ทำเอง"}}}
    off = mp.clean_program({"jobs": {"cooling": {"on": False, "months": "", "hours": "", "level": "ทำเอง"}}}, cur)   # off needs no interval
    assert off["jobs"]["cooling"]["on"] is False
    for bad in ({"label": ""}, {"step_months": 0}, {"step_months": 13}, {"jobs": {"nope": {}}},
                {"jobs": {"cooling": {"on": True, "months": "", "hours": ""}}}, {"jobs": {"cooling": {"on": True, "months": 3, "level": "บางที"}}},
                {"jobs": {"cooling": {"on": True, "months": "x"}}}, {}):
        with pytest.raises(mc.InvalidMachinery):
            mp.clean_program(bad, cur)
    with pytest.raises(mc.InvalidMachinery):
        mp.clean_program({}, None)                                              # a new program needs a name


# ------------------------------------------------------------------------ chart
def test_the_chart_marks_today_and_the_rounds_each_job_falls_due():
    p = plan(events=[ev("cooling", "2026-09-30"), ev("condenser", "2026-03-31"), ev("oil", "2026-10-20")], chart=True)
    c = p["chart"]
    assert len(c["columns"]) == mp.CHART_ROUNDS + 1 and c["columns"][0]["label"] == "ตอนนี้"
    assert c["columns"][1]["date"] == "2027-01-29"                               # 3 months on (rounded days)
    rows = {r["key"]: r for r in c["rows"]}
    cooling = rows["cooling"]["cells"]                                          # done 30 Sep, every 3 months: due 30 Dec, 30 Mar, ...
    assert cooling[0] == "done" and cooling[1] == "due" and cooling[2] == "due"
    condenser = rows["condenser"]["cells"]                                      # overdue: red now, then every 6 months from today
    assert condenser[0] == "overdue" and condenser[1] is None and condenser[2] == "due" and condenser[4] == "due"
    assert rows["freezer_wash"]["cells"][0] == "unknown"                        # no record
    assert rows["oil"]["cells"][0] == "done"


def test_the_chart_shows_a_never_overhauled_compressor():
    c = plan({**M, "never_overhauled": True}, chart=True)["chart"]
    row = next(r for r in c["rows"] if r["key"] == "overhaul")
    assert row["cells"][0] == "never" and row["cells"][1:] == [None] * mp.CHART_ROUNDS     # 24,000 h is longer than 2 years of rounds


# -------------------------------------------------------------- "ทำแล้ว" form
def test_jobs_done_form_is_checked():
    jobs = mp.jobs_for(PROGRAMS["TUBE"])
    v = mp.clean_jobs_done({"done_date": "2026-10-29", "job_keys": ["oil", "grease", "oil"], "cost": "1,500", "hours_at": "", "performed_by": " ช่างเอ๋ "}, jobs, today=TODAY)
    assert v["job_keys"] == ["oil", "grease"] and v["cost"] == 1500 and v["hours_at"] is None and v["performed_by"] == "ช่างเอ๋"
    for bad in ({"done_date": "", "job_keys": ["oil"]}, {"done_date": "2026-11-30", "job_keys": ["oil"]}, {"done_date": "2569-10-01", "job_keys": ["oil"]},
                {"done_date": "2026-10-01", "job_keys": []}, {"done_date": "2026-10-01", "job_keys": ["nope"]}):
        with pytest.raises(mc.InvalidMachinery):
            mp.clean_jobs_done(bad, jobs, today=TODAY)


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
    base = {"id": "x", "branch": "ส", "kind": "เครื่อง", "number": 1, "name": "เครื่อง 1"}
    v = mc.clean_machine({"hours_per_day": "20", "days_per_week": "6", "never_overhauled": True}, existing=base, others=[])
    assert v["hours_per_day"] == 20 and v["days_per_week"] == 6 and v["never_overhauled"] is True
    for bad in ({"hours_per_day": 30}, {"days_per_week": 0}, {"days_per_week": 9}):
        with pytest.raises(mc.InvalidMachinery):
            mc.clean_machine(bad, existing=base, others=[])
