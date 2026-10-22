from datetime import date

import pytest

from app import mgr_recurring as r


def rule(**kw):
    return r.clean_rule(kw)


def test_every_day_and_every_n_days():
    assert r.occurrences(rule(unit="day"), date(2026, 10, 24), date(2026, 10, 27)) == [date(2026, 10, 24 + i) for i in range(4)]
    assert r.occurrences(rule(unit="day", every=3), date(2026, 10, 24), date(2026, 11, 3)) == [date(2026, 10, 24), date(2026, 10, 27), date(2026, 10, 30), date(2026, 11, 2)]


def test_weekdays():
    mon_thu = rule(unit="week", weekdays=[0, 3])
    assert r.occurrences(mon_thu, date(2026, 10, 26), date(2026, 11, 8)) == [date(2026, 10, 26), date(2026, 10, 29), date(2026, 11, 2), date(2026, 11, 5)]
    assert r.first_on_or_after(mon_thu, date(2026, 10, 27)) == date(2026, 10, 29)          # a Tuesday: the next one is Thursday


def test_every_two_weeks_keeps_its_grid_from_the_start_date():
    fortnight = rule(unit="week", every=2, weekdays=[4])                                   # Fridays, every second week
    got = r.occurrences(fortnight, date(2026, 10, 23), date(2026, 12, 1))
    assert got == [date(2026, 10, 23), date(2026, 11, 6), date(2026, 11, 20), date(2026, 12, 4)][:3]


def test_days_of_the_month_including_the_last_and_short_months():
    m = rule(unit="month", month_days=[1, 15, "last"])
    assert r.occurrences(m, date(2026, 2, 1), date(2026, 2, 28)) == [date(2026, 2, 1), date(2026, 2, 15), date(2026, 2, 28)]
    on31 = rule(unit="month", month_days=[31])
    assert r.occurrences(on31, date(2026, 4, 1), date(2026, 5, 31)) == [date(2026, 4, 30), date(2026, 5, 31)]       # April has 30 days


def test_every_three_months():
    q = rule(unit="month", every=3, month_days=[5])
    assert r.occurrences(q, date(2026, 10, 5), date(2027, 7, 31)) == [date(2026, 10, 5), date(2027, 1, 5), date(2027, 4, 5), date(2027, 7, 5)]


def test_the_nth_weekday_of_the_month():
    first_monday = rule(unit="month", nth={"n": 1, "weekday": 0})
    assert r.occurrences(first_monday, date(2026, 10, 1), date(2026, 12, 31)) == [date(2026, 10, 5), date(2026, 11, 2), date(2026, 12, 7)]
    last_friday = rule(unit="month", nth={"n": -1, "weekday": 4})
    assert r.occurrences(last_friday, date(2026, 10, 1), date(2026, 11, 30)) == [date(2026, 10, 30), date(2026, 11, 27)]


def test_yearly():
    y = rule(unit="year", month=1, day=15)
    assert r.occurrences(y, date(2026, 10, 24), date(2029, 1, 31)) == [date(2027, 1, 15), date(2028, 1, 15), date(2029, 1, 15)]
    leap = rule(unit="year", month=2, day=29)
    assert r.occurrences(leap, date(2026, 1, 1), date(2028, 12, 31)) == [date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29)]


@pytest.mark.parametrize("bad", [
    None, {}, {"unit": "hour"}, {"unit": "week"}, {"unit": "week", "weekdays": [9]}, {"unit": "month"}, {"unit": "month", "month_days": [40]},
    {"unit": "month", "nth": {"n": 0, "weekday": 1}}, {"unit": "year", "month": 13, "day": 1}, {"unit": "day", "every": 0}, {"unit": "day", "every": "x"},
])
def test_bad_rules_are_refused(bad):
    with pytest.raises(r.InvalidRule):
        r.clean_rule(bad)


def test_describe_in_thai():
    assert r.describe(rule(unit="day")) == "ทุกวัน" and r.describe(rule(unit="day", every=3)) == "ทุก 3 วัน"
    assert r.describe(rule(unit="week", weekdays=[0, 3])) == "ทุกสัปดาห์ วันจันทร์, วันพฤหัสบดี"
    assert r.describe(rule(unit="week", every=2, weekdays=[4])) == "ทุก 2 สัปดาห์ วันศุกร์"
    assert r.describe(rule(unit="month", month_days=[1, "last"])) == "ทุกเดือน วันที่ 1, สุดท้าย"
    assert r.describe(rule(unit="month", nth={"n": 1, "weekday": 0})) == "ทุกเดือน วันจันทร์ที่ 1ของเดือน"
    assert r.describe(rule(unit="year", month=1, day=15)) == "ทุกปี 15 ม.ค."


# ------------------------------------------------------------ what a template creates today
def tpl(**kw):
    return {"active": True, "rule": rule(unit="week", weekdays=[0]), "next_due": "2026-10-26", "lead_days": 0, "mode": "calendar", **kw}


def test_nothing_is_created_before_the_due_date():
    assert r.to_create(tpl(), date(2026, 10, 25)) == ([], date(2026, 10, 26))


def test_the_ticket_is_created_on_the_day_and_the_next_one_is_set():
    assert r.to_create(tpl(), date(2026, 10, 26)) == ([date(2026, 10, 26)], date(2026, 11, 2))


def test_lead_days_create_it_early():
    assert r.to_create(tpl(lead_days=3), date(2026, 10, 23)) == ([date(2026, 10, 26)], date(2026, 11, 2))
    assert r.to_create(tpl(lead_days=3), date(2026, 10, 22)) == ([], date(2026, 10, 26))


def test_occurrences_missed_for_a_long_time_are_skipped_not_piled_up():
    made, nxt = r.to_create(tpl(next_due="2026-09-07"), date(2026, 10, 26))      # the app was off for seven weeks
    assert made == [date(2026, 10, 26)] and nxt == date(2026, 11, 2)


def test_a_paused_template_or_one_past_its_end_creates_nothing():
    assert r.to_create(tpl(active=False), date(2026, 10, 30)) == ([], date(2026, 10, 26))
    made, nxt = r.to_create(tpl(end_date="2026-10-26"), date(2026, 10, 26))
    assert made == [date(2026, 10, 26)] and nxt is None


def test_after_done_makes_one_at_a_time_and_counts_from_completion():
    t = tpl(mode="after_done", rule=rule(unit="day", every=30))
    assert r.to_create(t, date(2026, 10, 26)) == ([date(2026, 10, 26)], None)              # next_due stays empty while it is open
    assert r.next_after_done(t, date(2026, 11, 3)) == date(2026, 12, 3)                      # 30 days after it was finished
