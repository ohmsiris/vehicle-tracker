from datetime import date, datetime, timedelta

import pytest

from app import mgr_jobs as j
from app import service_plan as sp

TODAY = date(2026, 10, 24)
VID = "11111111-1111-1111-1111-111111111111"
S2 = sp.merge_groups(None)["S2"]


def oil(months=2.0, km=10_000):
    return {"key": "engine_oil", "label": "น้ำมันเครื่อง", "group": "x", "interval_km": km, "interval_months": months}


def rec(key, when, odo=None):
    return {"item_key": key, "done_date": when, "odometer_km": odo, "source": "manual"}


# ------------------------------------------------------------ the calculator
def test_progress_is_how_much_of_the_interval_is_used():
    s = sp.item_status(oil(), {"date": date(2026, 9, 24), "source": "manual"}, None, None, TODAY)        # 30 of 60 days
    assert s["progress"] == pytest.approx(0.49, abs=0.02) and s["state"] == "ok"
    over = sp.item_status(oil(), {"date": date(2026, 8, 1), "source": "manual"}, None, None, TODAY)
    assert over["progress"] > 1 and over["state"] == "overdue"
    assert sp.item_status(oil(), None, None, None, TODAY)["progress"] is None


def test_real_km_can_use_up_more_of_the_interval_than_time_has():
    odo = {"date": TODAY, "km": 59_000}
    s = sp.item_status(oil(months=6), {"date": date(2026, 10, 1), "odometer_km": 50_000, "source": "manual"}, None, odo, TODAY)
    assert s["progress"] == 0.9 and s["km_left"] == 1000


def test_without_real_distance_km_left_is_an_estimate_from_the_group():
    s = sp.item_status(oil(), {"date": date(2026, 10, 4), "source": "manual"}, None, None, TODAY, est_kmpd=164.5)      # 20 days
    assert s["km_left"] is None and s["est_km_since"] == 3290 and s["est_km_left"] == 6710


def test_the_coming_close_window_is_adjustable():
    last = {"date": date(2026, 9, 1), "source": "manual"}                  # due 2026-11-01: 8 days away
    assert sp.item_status(oil(), last, None, None, TODAY, soon_days=10)["state"] == "soon"
    assert sp.item_status(oil(), last, None, None, TODAY, soon_days=5)["state"] == "ok"
    assert sp.SOON_DAYS == 10


# ----------------------------------------------------------------- snoozing
def plan(snoozes=None, today=TODAY, records=None):
    records = records if records is not None else [rec("engine_oil", "2026-09-01")]                 # due 2026-11-01
    return sp.vehicle_plan({"id": VID}, group=S2, records=records, readings=[], overrides=[], today=today, snoozes=snoozes)


def item(p, key="engine_oil"):
    return next(i for i in p["items"] if i["key"] == key)


def test_a_snooze_marks_the_item_until_its_date():
    p = plan({"engine_oil": {"until": "2026-10-30", "for_due": "2026-11-01"}})
    assert item(p)["snoozed_until"] == "2026-10-30" and item(p)["snooze_ended"] is None


def test_an_ended_snooze_is_remembered_for_the_reminder_key():
    p = plan({"engine_oil": {"until": "2026-10-20", "for_due": "2026-11-01"}})
    assert item(p)["snoozed_until"] is None and item(p)["snooze_ended"] == "2026-10-20"


def test_a_snooze_for_an_old_due_date_no_longer_applies():
    p = plan({"engine_oil": {"until": "2026-12-30", "for_due": "2026-09-01"}})              # the oil was changed since
    assert item(p)["snoozed_until"] is None and item(p)["snooze_ended"] is None


def test_clean_snooze():
    row = sp.clean_snooze({"vehicle_id": VID, "item_key": "engine_oil", "days": "7"}, today=TODAY)
    assert row == {"vehicle_id": VID, "item_key": "engine_oil", "until": "2026-10-31"}
    for bad in ({"days": 0}, {"days": 91}, {"days": "x"}, {"item_key": "nope"}, {"vehicle_id": "x"}):
        with pytest.raises(sp.InvalidPlan):
            sp.clean_snooze({"vehicle_id": VID, "item_key": "engine_oil", "days": 3, **bad}, today=TODAY)


# --------------------------------------------------------------- reminders
def row(**kw):
    p = plan(**kw)
    return {"id": VID, "plate_number": "1ฒถ 8966", "vehicle_number": "1", "branch": "สระบุรี", **p}


def collect(rows, today=TODAY, soon=10, items=("engine_oil",)):
    return sp.collect_reminders(rows, today, soon, list(items))


def test_the_stages_are_soon_then_due_then_overdue():
    assert collect([row(today=date(2026, 10, 20))], today=date(2026, 10, 20)) == []      # due 2026-11-01: 12 days away, not yet
    soon = collect([row(today=date(2026, 10, 24))])[0]
    assert soon["stage"] == "soon" and soon["days_left"] == 8 and soon["truck"] == "#1 1ฒถ 8966"
    due = collect([row(today=date(2026, 11, 1))], today=date(2026, 11, 1))[0]
    assert due["stage"] == "due" and due["days_left"] == 0
    over = collect([row(today=date(2026, 11, 4))], today=date(2026, 11, 4))[0]
    assert over["stage"] == "overdue" and over["days_left"] == -3


def test_the_window_edges_are_ten_days_then_zero():
    assert collect([row(today=date(2026, 10, 22))], today=date(2026, 10, 22))[0]["days_left"] == 10          # exactly 10: in
    assert collect([row(today=date(2026, 10, 21))], today=date(2026, 10, 21)) == []                          # 11: out


def test_each_stage_has_its_own_key_and_overdue_repeats_every_three_days():
    keys = {}
    for d in (date(2026, 10, 24), date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 7)):
        keys[d] = collect([row(today=d)], today=d)[0]["key"]
    assert keys[date(2026, 10, 24)].endswith(":2026-11-01:soon") and keys[date(2026, 11, 1)].endswith(":due")
    assert keys[date(2026, 11, 2)] == keys[date(2026, 11, 3)] != keys[date(2026, 11, 4)]                   # one nudge per three days
    assert keys[date(2026, 11, 4)] == keys[date(2026, 11, 5)] == keys[date(2026, 11, 6)] != keys[date(2026, 11, 7)]


def test_snoozed_items_are_left_out_and_come_back_with_a_new_key():
    during = row(snoozes={"engine_oil": {"until": "2026-10-30", "for_due": "2026-11-01"}})
    assert collect([during]) == []
    after = row(snoozes={"engine_oil": {"until": "2026-10-20", "for_due": "2026-11-01"}})
    got = collect([after])[0]
    assert got["key"].endswith(":soon:s2026-10-20")                                                       # so a "soon" already sent is sent again


def test_only_the_chosen_items_and_known_items_are_reminded():
    r = row(records=[rec("engine_oil", "2026-09-01"), rec("gearbox_oil", "2026-02-01")])
    assert [x["item_key"] for x in collect([r], items=("engine_oil", "gearbox_oil"))] == ["gearbox_oil", "engine_oil"]       # overdue first
    assert [x["item_key"] for x in collect([r])] == ["engine_oil"]
    never = row(records=[])
    assert collect([never]) == []                                                                         # nothing is known about it


def test_the_message():
    rows = [row(today=date(2026, 11, 1)), {**row(today=date(2026, 11, 1)), "id": "2", "plate_number": "84-2682", "vehicle_number": "27"}]
    rows[1]["items"] = [{**rows[1]["items"][0], "days_left": -3, "state": "overdue", "due_date": "2026-10-29", "est_km_left": -480, "km_left": None}]
    rows[0]["items"] = [{**rows[0]["items"][0], "est_km_left": 0}]
    found = collect(rows, today=date(2026, 11, 1))
    text = sp.reminder_text(found, date(2026, 11, 1), 10)
    assert text.splitlines()[0] == "เตือนรอบซ่อมบำรุงรถ · 1 พ.ย. 2569"
    assert "ครบกำหนดวันนี้ (1)" in text and "• #1 1ฒถ 8966 — น้ำมันเครื่อง · เหลือ ≈ 0 กม." in text
    assert "เลยกำหนดแล้ว (1)" in text and "• #27 84-2682 — น้ำมันเครื่อง · เลย 3 วัน · เกิน ≈ 480 กม." in text


def test_as_of_is_today_or_a_bounded_future_day():
    assert sp.parse_as_of("", today=TODAY) is None and sp.parse_as_of("2026-10-24", today=TODAY) is None
    assert sp.parse_as_of("2026-12-31", today=TODAY) == date(2026, 12, 31)
    for bad in ("2026-10-23", "2028-12-31", "junk"):
        with pytest.raises(sp.InvalidPlan):
            sp.parse_as_of(bad, today=TODAY)


def test_reminder_settings_are_validated():
    assert sp.clean_reminder_settings({"soon_days": "14", "enabled": False, "items": ["engine_oil", "engine_oil", "coolant"]}) == {
        "plan_soon_days": 14, "plan_reminders": False, "plan_remind_items": ["engine_oil", "coolant"]}
    for bad in ({"soon_days": 0}, {"soon_days": 99}, {"soon_days": "x"}, {"items": ["nope"]}, {"items": "engine_oil"}):
        with pytest.raises(sp.InvalidPlan):
            sp.clean_reminder_settings(bad)


# ------------------------------------------------------------ the daily push
class Fakes:
    def __init__(self, found, settings=None, fail=False):
        self.found, self.fail, self.sent, self.claimed = found, fail, [], set()
        self.settings = settings or {"plan_reminders": True, "digest_time": "08:00", "plan_soon_days": 10}

    def claim(self, key):
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    def release(self, key):
        self.claimed.discard(key)

    def push(self, text):
        if self.fail:
            raise RuntimeError("down")
        self.sent.append(text)

    def run(self, now):
        return j.run_plan_reminders(now, settings=self.settings, compute=lambda d: self.found, text=lambda f, d, s: f"{len(f)} items",
                                    claim=self.claim, release=self.release, push=self.push)


FOUND = [{"key": "k1"}, {"key": "k2"}]


def test_the_plan_reminders_go_once_a_day_after_the_summary_time():
    f = Fakes(FOUND)
    assert f.run(datetime(2026, 10, 24, 7, 59)) == 0
    assert f.run(datetime(2026, 10, 24, 8, 0)) == 2 and f.sent == ["2 items"]
    assert f.run(datetime(2026, 10, 24, 9, 0)) == 0                              # already done today
    f.found = [{"key": "k2"}, {"key": "k3"}]
    assert f.run(datetime(2026, 10, 25, 8, 0)) == 1                              # tomorrow: only what is new (k2 was sent already)


def test_a_failed_push_is_retried_with_nothing_lost():
    f = Fakes(FOUND, fail=True)
    assert f.run(datetime(2026, 10, 24, 8, 0)) == 0 and f.claimed == set()
    f.fail = False
    assert f.run(datetime(2026, 10, 24, 8, 1)) == 2


def test_the_plan_reminders_can_be_switched_off():
    f = Fakes(FOUND, settings={"plan_reminders": False, "digest_time": "08:00"})
    assert f.run(datetime(2026, 10, 24, 12, 0)) == 0 and f.sent == []
