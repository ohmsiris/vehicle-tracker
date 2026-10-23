from datetime import date

from app import home as h

TODAY = date(2026, 10, 23)


def req(status, **kw):
    return {"status": status, "truck_number": "12", "plate": "70-1234", "shop": None, "problem": "", "days_at_shop": None, "days_in_status": 0, **kw}


def test_repairs_counts_each_stage_and_lists_the_longest_at_shop_first():
    rows = [req("open"), req("open"), req("awaiting_bill", days_in_status=9),
            req("at_shop", truck_number="3", shop="อู่เอ๋", days_at_shop=2), req("at_shop", truck_number="7", shop="อีซูซุ", days_at_shop=15),
            req("completed"), req("cancelled")]
    out = h.build_repairs(rows)
    assert (out["open"], out["at_shop"], out["awaiting_bill"]) == (2, 2, 1)
    assert out["longest_at_shop_days"] == 15 and out["oldest_awaiting_days"] == 9
    assert [r["truck"] for r in out["at_shop_list"]] == ["7", "3"]


def test_repairs_with_nothing_going_on():
    out = h.build_repairs([])
    assert out["ready"] and out["open"] == out["at_shop"] == out["awaiting_bill"] == 0 and out["at_shop_list"] == []


def test_spend_splits_this_and_last_month_and_ignores_older_rows():
    rows = [{"d": "2026-10-02", "t": 100}, {"d": "2026-10-20", "t": "50.5"}, {"d": "2026-09-30", "t": 70},
            {"d": "2026-08-31", "t": 999}, {"d": None, "t": 5}, {"d": "2026-10-05", "t": None}]
    out = h.build_spend(rows, "t", "d", TODAY)
    assert out == {"this": 150.5, "last": 70.0, "count_this": 3, "count_last": 1}


def test_last_month_across_a_new_year():
    assert h.prev_month_start(date(2026, 1, 15)) == date(2025, 12, 1)
    out = h.build_spend([{"d": "2025-12-31", "t": 10}], "t", "d", date(2026, 1, 3))
    assert out["last"] == 10.0 and out["this"] == 0


def test_stock_lists_only_tracked_items_below_their_minimum_emptiest_first():
    items = [{"id": "a", "name": "ลูกปืน", "unit": "ตัว", "min_qty": 10, "active": True},
             {"id": "b", "name": "สายพาน", "unit": "เส้น", "min_qty": 4, "active": True},
             {"id": "c", "name": "จารบี", "unit": "กก.", "min_qty": 2, "active": True},
             {"id": "d", "name": "เลิกใช้", "unit": None, "min_qty": 5, "active": False},
             {"id": "e", "name": "ไม่ติดตาม", "unit": None, "min_qty": None, "active": True}]
    out = h.build_stock(items, {"a": 5, "b": 0, "c": 9, "d": 0})
    assert out["tracked"] == 3 and out["low_count"] == 2
    assert [x["name"] for x in out["low"]] == ["สายพาน", "ลูกปืน"]


def test_machinery_counts_recent_events_and_names_the_machine():
    machines = [{"id": "m1", "name": "x", "kind": "เครื่อง", "number": 3}]
    events = [{"event_date": "2026-10-20", "machine_id": "m1", "kind": "ซ่อม", "summary": "เปลี่ยนซีล"},
              {"event_date": "2026-08-01", "machine_id": None, "area": "ปั๊มคลอรีน", "kind": "ตรวจเช็ค", "summary": "เช็คท่อ"}]
    out = h.build_machinery(machines, events, TODAY)
    assert out["machines"] == 1 and out["events_recent"] == 1
    assert out["latest"][0]["where"] == "เครื่อง 3" and out["latest"][1]["where"] == "ปั๊มคลอรีน"


def test_one_card_failing_does_not_take_the_page_down(monkeypatch):
    def boom(today):
        raise RuntimeError("db down")
    monkeypatch.setattr(h, "CARDS", {"ok": lambda today: {"ready": True, "n": 1}, "bad": boom})
    monkeypatch.setattr(h, "_cache", {"at": 0.0, "value": None})
    out = h.load(fresh=True)
    assert out["ok"] == {"ready": True, "n": 1}
    assert out["bad"] == {"ready": False, "error": True}
    assert out["soon"] == h.HOME_SOON and out["as_of"]


def test_the_page_answers_from_a_short_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(h, "CARDS", {"ok": lambda today: calls.append(1) or {"ready": True}})
    monkeypatch.setattr(h, "_cache", {"at": 0.0, "value": None})
    h.load()
    h.load()
    assert len(calls) == 1
    h.load(fresh=True)
    assert len(calls) == 2


def test_coming_soon_cards_say_what_they_need():
    assert len({s["key"] for s in h.HOME_SOON}) == len(h.HOME_SOON)
    assert all(s["title"] and s["blurb"] and s["needs"] for s in h.HOME_SOON)
