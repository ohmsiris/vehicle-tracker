import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import dashboard as dash

TODAY = date(2026, 10, 20)


def ev(event_id, event_date, total, *, vehicle_id="v1", plate="84-4786", truck="36", branch="สระบุรี", vendor="ชมรม", source="bill"):
    d = date.fromisoformat(event_date)
    return {"event_id": event_id, "event_date": event_date, "event_month": d.replace(day=1).isoformat(), "vehicle_id": vehicle_id,
            "plate_number": plate, "vehicle_number": truck, "vehicle_branch": branch, "vendor": vendor, "total_cost": total, "source": source}


def line(event_date, category, amount, *, en=None, branch="สระบุรี"):
    d = date.fromisoformat(event_date)
    return {"event_month": d.replace(day=1).isoformat(), "category": category, "category_en": en, "line_total": amount, "vehicle_branch": branch}


def veh(vid, plate, truck, branch="สระบุรี", *, last=None, open_=0, awaiting=0):
    return {"vehicle_id": vid, "plate_number": plate, "vehicle_number": truck, "branch": branch, "open_requests": open_,
            "awaiting_bill_requests": awaiting, "last_service_date": last, "last_known_mileage": None, "next_service_mileage": None}


def req(status, *, branch="สระบุรี", days_in_status=None, days_to_fix=None, fixed_date=None, no_bill=False, changed="2026-10-01"):
    return {"status": status, "branch": branch, "days_in_status": days_in_status, "days_to_fix": days_to_fix, "fixed_date": fixed_date,
            "no_bill": no_bill, "status_changed_date": changed}


def build(**kw):
    base = dict(events=[], lines=[], vehicles=[], requests=[], today=TODAY, months=12, branch=None)
    base.update(kw)
    return dash.build_dashboard(**base)


# ------------------------------------------------------------------ months
def test_add_months_crosses_year_boundaries():
    assert dash.add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert dash.add_months(date(2026, 11, 1), 3) == date(2027, 2, 1)
    assert dash.add_months(date(2026, 10, 1), -11) == date(2025, 11, 1)


def test_monthly_series_has_every_month_including_empty_ones():
    d = build(events=[ev("a", "2026-10-05", 1000), ev("b", "2026-10-19", 500), ev("c", "2026-07-02", 300)], months=4)
    assert [m["month"] for m in d["monthly"]] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert [m["spend"] for m in d["monthly"]] == [300, 0, 0, 1500]
    assert [m["count"] for m in d["monthly"]] == [1, 0, 0, 2]


def test_events_outside_the_window_are_excluded_everywhere():
    d = build(events=[ev("old", "2025-01-10", 9999), ev("new", "2026-10-01", 100)],
              lines=[line("2025-01-10", "เบรก", 9999), line("2026-10-01", "เบรก", 100)], months=6)
    assert d["kpis"]["spend_period"] == 100 and d["kpis"]["repairs_period"] == 1
    assert d["by_category"] == [{"category": "เบรก", "category_en": None, "amount": 100, "share": 1.0}]
    assert [t["amount"] for t in d["top_trucks"]] == [100]


def test_this_month_vs_last_month():
    d = build(events=[ev("a", "2026-10-02", 700), ev("b", "2026-09-30", 250)], months=3)
    k = d["kpis"]
    assert (k["spend_this_month"], k["spend_last_month"], k["repairs_this_month"]) == (700, 250, 1)


@pytest.mark.parametrize("asked, used", [(0, 1), (-5, 1), (12, 12), (999, 60)])
def test_months_is_clamped(asked, used):
    assert build(months=asked)["months"] == used and len(build(months=asked)["monthly"]) == used


# -------------------------------------------------------------- money rules
def test_missing_or_odd_costs_count_as_zero_but_still_as_a_repair():
    d = build(events=[ev("a", "2026-10-01", None), ev("b", "2026-10-02", "1500.50"), ev("c", "2026-10-03", "oops", source="manual")])
    assert d["kpis"]["spend_this_month"] == 1500.5
    assert d["kpis"]["repairs_this_month"] == 3
    assert d["notes"]["events_without_cost"] == 2


def test_categories_sorted_with_shares_and_unlabelled_bucket():
    d = build(lines=[line("2026-10-01", "เบรก", 600, en="Brakes"), line("2026-10-01", "ค่าแรง", 300, en="Labor"),
                     line("2026-10-02", None, 100), line("2026-10-02", "เบรก", 0), line("2026-10-02", "เบรก", None)])
    assert [c["category"] for c in d["by_category"]] == ["เบรก", "ค่าแรง", "ไม่ระบุหมวด"]
    assert d["by_category"][0] == {"category": "เบรก", "category_en": "Brakes", "amount": 600, "share": 0.6}
    assert round(sum(c["share"] for c in d["by_category"]), 4) == 1.0


def test_top_trucks_and_vendors():
    events = [ev("1", "2026-10-01", 1000, vehicle_id="v1", plate="A", truck="1", vendor="ชมรม"),
              ev("2", "2026-09-01", 2000, vehicle_id="v2", plate="B", truck="2", vendor="โชค"),
              ev("3", "2026-10-09", 500, vehicle_id="v1", plate="A", truck="1", vendor="ชมรม"),
              ev("4", "2026-10-10", 100, vehicle_id="v3", plate="C", truck="3", vendor=" "),
              ev("5", "2026-10-11", 0, vehicle_id="v4", plate="D", truck="4", vendor=None)]
    d = build(events=events)
    assert [(t["plate"], t["amount"], t["count"], t["last_date"]) for t in d["top_trucks"][:3]] == [
        ("B", 2000, 1, "2026-09-01"), ("A", 1500, 2, "2026-10-09"), ("C", 100, 1, "2026-10-10")]
    assert d["top_vendors"] == [{"vendor": "โชค", "amount": 2000, "count": 1}, {"vendor": "ชมรม", "amount": 1500, "count": 2}]


def test_top_lists_are_capped():
    events = [ev(str(i), "2026-10-01", 10 + i, vehicle_id=f"v{i}", plate=f"P{i}", vendor=f"shop{i}") for i in range(30)]
    d = build(events=events)
    assert len(d["top_trucks"]) == dash.TOP_TRUCKS and len(d["top_vendors"]) == dash.TOP_VENDORS
    assert d["top_trucks"][0]["amount"] == 39      # biggest first


# ------------------------------------------------------------------ branch
def test_branch_filter_applies_to_every_section():
    events = [ev("a", "2026-10-01", 100, branch="สระบุรี"), ev("b", "2026-10-01", 900, vehicle_id="v2", plate="B", branch="แก่งคอย")]
    lines = [line("2026-10-01", "เบรก", 100, branch="สระบุรี"), line("2026-10-01", "เบรก", 900, branch="แก่งคอย")]
    vehicles = [veh("v1", "A", "1", "สระบุรี"), veh("v2", "B", "2", "แก่งคอย")]
    requests = [req("open", branch="สระบุรี"), req("open", branch="แก่งคอย"), req("open", branch="แก่งคอย")]
    d = build(events=events, lines=lines, vehicles=vehicles, requests=requests, branch="แก่งคอย")
    assert d["kpis"]["spend_period"] == 900 and d["kpis"]["open_requests"] == 2
    assert [c["amount"] for c in d["by_category"]] == [900]
    assert [f["plate"] for f in d["fleet"]] == ["B"] and d["branch"] == "แก่งคอย"
    assert build(events=events, branch=None)["kpis"]["spend_period"] == 1000


# ------------------------------------------------------------------- fleet
def test_fleet_flags_and_the_90_day_boundary():
    vehicles = [veh("a", "A", "1", last="2026-07-22"),     # 90 days before 2026-10-20 -> stale
                veh("b", "B", "2", last="2026-07-23"),     # 89 days -> fine
                veh("c", "C", "3", last=None),             # no record at all
                veh("d", "D", "4", last="2026-10-10", open_=2),
                veh("e", "E", "5", last="2026-10-10", awaiting=1)]
    flags = {f["plate"]: f["flags"] for f in build(vehicles=vehicles)["fleet"]}
    assert flags == {"A": ["not_serviced_lately"], "B": [], "C": ["no_history"], "D": ["open_requests"], "E": ["awaiting_bill"]}
    days = {f["plate"]: f["days_since_service"] for f in build(vehicles=vehicles)["fleet"]}
    assert days["A"] == 90 and days["B"] == 89 and days["C"] is None


def test_fleet_order_puts_the_things_needing_action_first():
    vehicles = [veh("q", "QUIET", "9", last="2026-10-15"), veh("n", "NOHIST", "8"), veh("s", "STALE", "7", last="2026-01-01"),
                veh("w", "AWAIT", "6", last="2026-10-15", awaiting=1), veh("o", "OPEN", "5", last="2026-10-15", open_=1)]
    assert [f["plate"] for f in build(vehicles=vehicles)["fleet"]] == ["OPEN", "AWAIT", "STALE", "NOHIST", "QUIET"]


def test_fleet_carries_each_trucks_period_spend():
    events = [ev("a", "2026-10-01", 300, vehicle_id="v1"), ev("b", "2026-09-01", 200, vehicle_id="v1")]
    f = build(events=events, vehicles=[veh("v1", "A", "1", last="2026-10-01"), veh("v2", "B", "2")])["fleet"]
    spend = {x["plate"]: (x["spend_period"], x["repairs_period"]) for x in f}
    assert spend == {"A": (500, 2), "B": (0, 0)}


# ---------------------------------------------------------------- requests
def test_request_kpis():
    requests = [req("open"), req("open"),
                req("awaiting_bill", days_in_status=3, days_to_fix=2, fixed_date="2026-10-14"),
                req("awaiting_bill", days_in_status=9, days_to_fix=4, fixed_date="2026-10-10"),
                req("completed", no_bill=True, days_to_fix=6, fixed_date="2026-10-05", changed="2026-10-06"),
                req("completed", no_bill=False, days_to_fix=0, fixed_date="2026-10-02", changed="2026-10-03"),
                req("completed", no_bill=True, days_to_fix=1, fixed_date="2024-01-01", changed="2024-01-02"),   # long ago: outside the window
                req("cancelled", days_to_fix=None)]
    k = build(requests=requests, months=6)["kpis"]
    assert (k["open_requests"], k["awaiting_bill"], k["oldest_awaiting_days"]) == (2, 2, 9)
    assert k["closed_without_bill"] == 1                 # the 2024 one is outside the window
    assert k["avg_days_to_fix"] == 3.0                   # (2 + 4 + 6 + 0) / 4


def test_empty_database_gives_zeros_not_errors():
    d = build()
    assert d["kpis"]["spend_period"] == 0 and d["kpis"]["avg_days_to_fix"] is None and d["kpis"]["oldest_awaiting_days"] == 0
    assert d["by_category"] == [] and d["top_trucks"] == [] and d["top_vendors"] == [] and d["fleet"] == []
    assert len(d["monthly"]) == 12 and all(m["spend"] == 0 and m["count"] == 0 for m in d["monthly"])


# ---------------------------------------------------------------- plumbing
def test_select_all_pages_until_a_short_page(monkeypatch):
    from app import supabase
    data = list(range(5))
    calls = []

    def fake_select(table, params=None):
        calls.append((params["limit"], params["offset"]))
        off, lim = int(params["offset"]), int(params["limit"])
        return data[off:off + lim]
    monkeypatch.setattr(supabase, "select", fake_select)
    assert supabase.select_all("t", {"order": "id.asc"}, page=2) == data
    assert calls == [("2", "0"), ("2", "2"), ("2", "4")]
    calls.clear()
    assert supabase.select_all("t", {"order": "id.asc"}, page=5) == data     # an exactly-full page triggers one more (empty) read
    assert calls == [("5", "0"), ("5", "5")]
    with pytest.raises(supabase.SupabaseError):
        supabase.select_all("t", {})                                          # no order -> unstable paging -> refused


def _client(monkeypatch, configured=True):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: configured)
    return main, TestClient(main.app)


def test_dashboard_endpoint_demo_mode_runs_the_real_aggregation(monkeypatch):
    main, client = _client(monkeypatch, configured=False)
    assert client.get("/api/dashboard").status_code == 403
    assert client.get("/api/dashboard?token=secret").status_code == 503            # no demo flag, no database
    monkeypatch.setenv("DEV_DASHBOARD_DEMO", "1")
    d = client.get("/api/dashboard?token=secret&months=6").json()
    assert d["months"] == 6 and len(d["monthly"]) == 6 and d["kpis"]["spend_period"] > 0
    assert {f["plate"] for f in d["fleet"]} == {"84-4786", "81-3195", "1ฒถ 8966", "70-1234", "1กข 4267", "2กค 9999"}
    only = client.get("/api/dashboard?token=secret&branch=แก่งคอย").json()
    assert {f["branch"] for f in only["fleet"]} == {"แก่งคอย"} and only["branch"] == "แก่งคอย"
    never = next(f for f in d["fleet"] if f["plate"] == "2กค 9999")
    assert "no_history" in never["flags"]


def test_dashboard_endpoint_maps_database_errors(monkeypatch):
    main, client = _client(monkeypatch)

    def boom(*a, **k): raise main.supabase.SupabaseError("secret internals")
    monkeypatch.setattr(main.supabase, "select_all", boom)
    r = client.get("/api/dashboard?token=secret")
    assert r.status_code == 502 and "internals" not in r.text


# --------------------------------------- "repairs done": every repair counted ONCE
def rq(status, fixed="2026-10-10", *, has_record=False, no_bill=False, branch="สระบุรี"):
    return {"status": status, "branch": branch, "fixed_date": fixed, "has_record": has_record, "no_bill": no_bill,
            "days_to_fix": 1, "days_in_status": 1, "status_changed_date": fixed or "2026-10-10"}


def done(requests, events=(), **kw):
    return build(events=list(events), requests=requests, **kw)["kpis"]["done_this_month"]


def test_a_bill_and_the_request_it_closed_are_one_repair_not_two():
    d = done([rq("completed", has_record=True)], events=[ev("e1", "2026-10-10", 3000)])
    assert d == {"records": 1, "no_bill": 0, "awaiting": 0, "total": 1}


def test_one_bill_closing_two_requests_is_still_one_repair():
    d = done([rq("completed", has_record=True), rq("completed", has_record=True)], events=[ev("e1", "2026-10-10", 3000)])
    assert d["total"] == 1


def test_a_repair_nobody_wrote_on_the_foh_sheet_still_counts():
    assert done([], events=[ev("e1", "2026-10-10", 3000), ev("e2", "2026-10-12", 500, source="manual")])["total"] == 2


def test_closed_with_no_bill_counts_on_top_of_the_records():
    d = done([rq("completed", no_bill=True)], events=[ev("e1", "2026-10-10", 3000)])
    assert d == {"records": 1, "no_bill": 1, "awaiting": 0, "total": 2}


def test_fixed_and_waiting_for_a_bill_counts_now_and_not_again_when_the_bill_arrives():
    before = done([rq("awaiting_bill")])
    assert before == {"records": 0, "no_bill": 0, "awaiting": 1, "total": 1}
    # the bill is saved: the request links to it (has_record) and the event exists -> still ONE repair
    after = done([rq("completed", has_record=True)], events=[ev("e1", "2026-10-14", 2500)])
    assert after == {"records": 1, "no_bill": 0, "awaiting": 0, "total": 1}


def test_open_and_cancelled_requests_never_count_as_done():
    # a request cancelled from 'awaiting bill' keeps its fixed date -- it must still not count
    assert done([rq("open", fixed=None), rq("cancelled", fixed="2026-10-10"), rq("cancelled", fixed=None)])["total"] == 0


def test_a_completed_request_whose_bill_was_deleted_still_counts_as_a_repair():
    assert done([rq("completed", has_record=False, no_bill=False)])["total"] == 1


def test_no_record_repairs_are_counted_in_the_month_they_were_fixed():
    requests = [rq("completed", fixed="2026-09-20", no_bill=True), rq("completed", fixed="2026-10-02", no_bill=True)]
    d = build(requests=requests, months=3)
    by_month = {m["month"]: m["repairs"] for m in d["monthly"]}
    assert by_month == {"2026-08": 0, "2026-09": 1, "2026-10": 1}
    assert d["kpis"]["done_this_month"]["total"] == 1 and d["kpis"]["done_period"]["total"] == 2


def test_monthly_repairs_series_adds_records_and_no_record_repairs():
    d = build(events=[ev("a", "2026-10-03", 100), ev("b", "2026-10-04", 100)], requests=[rq("completed", no_bill=True)], months=2)
    assert [m["repairs"] for m in d["monthly"]] == [0, 3]
    assert [m["count"] for m in d["monthly"]] == [0, 2]       # `count` stays "bills / entries" (what spend is made of)


def test_done_count_respects_branch_and_window():
    requests = [rq("completed", no_bill=True, branch="สระบุรี"), rq("completed", no_bill=True, branch="แก่งคอย"),
                rq("completed", fixed="2024-01-01", no_bill=True)]
    assert build(requests=requests, branch="แก่งคอย", months=6)["kpis"]["done_period"]["total"] == 1
    assert build(requests=requests, months=6)["kpis"]["done_period"]["total"] == 2     # the 2024 one is outside the window
