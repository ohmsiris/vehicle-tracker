from datetime import date, datetime, timezone

import pytest

from app import mgr
from app import mgr_ai

TODAY = date(2026, 10, 24)
NOW = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
TYPES = {"chase", "finance", "fix", "other"}


def ct(data, existing=None):
    return mgr.clean_ticket(data, existing=existing, type_keys=TYPES)


def ticket(status="todo", **kw):
    return {"id": "t1", "title": "x", "status": status, "due_date": None, "due_time": None, **kw}


# ------------------------------------------------------------------ cleaning
def test_a_new_ticket_needs_a_title_and_gets_clean_values():
    v = ct({"title": "  โทรตาม   อะไหล่ชมรม ", "notes": " a \n\n b ", "place": "สระบุรี", "urgent": 1, "due_date": "2026-10-25", "due_time": "9:30"})
    assert v["title"] == "โทรตาม อะไหล่ชมรม" and v["notes"] == "a\nb" and v["urgent"] is True
    assert v["due_date"] == "2026-10-25" and v["due_time"] == "09:30" and v["checklist"] == []
    assert v["type_key"] is None and v["type_source"] is None                     # to be decided by the AI
    with pytest.raises(mgr.InvalidTicket, match="ชื่อเรื่อง"):
        ct({"title": "  "})


def test_a_type_chosen_by_hand_is_kept_and_marked_manual():
    v = ct({"title": "x", "type_key": "fix"})
    assert v["type_key"] == "fix" and v["type_source"] == "manual"
    assert ct({"title": "x", "type_key": "auto"})["type_key"] is None
    with pytest.raises(mgr.InvalidTicket, match="ประเภท"):
        ct({"title": "x", "type_key": "nope"})


@pytest.mark.parametrize("patch,message", [
    ({"due_date": "2569-10-25"}, "พ.ศ."), ({"due_date": "25/10/2026"}, "ไม่ถูกต้อง"), ({"due_time": "25:00"}, "ไม่ถูกต้อง"),
    ({"remind_on": "x"}, "ไม่ถูกต้อง"), ({"waiting_phone": "abc"}, "เบอร์"), ({"vehicle_id": "nope"}, "รถ"),
    ({"due_time": "10:00", "due_date": ""}, "วันกำหนด"),
])
def test_bad_values_are_refused(patch, message):
    with pytest.raises(mgr.InvalidTicket, match=message):
        ct({"title": "x", **patch})


def test_a_partial_edit_only_touches_what_was_sent():
    existing = ticket(title="old", due_date="2026-10-25", place="สระบุรี")
    assert ct({"urgent": True}, existing) == {"urgent": True}
    with pytest.raises(mgr.InvalidTicket):
        ct({"due_time": "10:00"}, ticket(due_date=None))                           # a time needs a day
    assert ct({"due_time": "10:00"}, existing) == {"due_time": "10:00"}


def test_the_checklist_is_cleaned():
    v = ct({"title": "x", "checklist": [{"text": " a ", "done": True}, {"text": ""}, "b", {"text": "c"}]})
    assert v["checklist"] == [{"text": "a", "done": True}, {"text": "b", "done": False}, {"text": "c", "done": False}]
    with pytest.raises(mgr.InvalidTicket):
        ct({"title": "x", "checklist": "a, b"})


# ------------------------------------------------------------ moving a ticket
def test_the_normal_path():
    assert mgr.transition(ticket("todo"), "start", today=TODAY, now=NOW)["status"] == "doing"
    waiting = mgr.transition(ticket("doing"), "wait", {"waiting_on": "ชมรมย์การช่าง", "waiting_phone": "081-928-5474", "remind_on": "2026-10-27"}, today=TODAY, now=NOW)
    assert waiting["status"] == "waiting" and waiting["waiting_on"] == "ชมรมย์การช่าง" and waiting["remind_on"] == "2026-10-27"
    assert mgr.transition(ticket("waiting"), "resume", today=TODAY, now=NOW)["status"] == "doing"
    done = mgr.transition(ticket("doing"), "done", today=TODAY, now=NOW)
    assert done["status"] == "done" and done["done_at"].startswith("2026-10-24")


def test_cancel_and_reopen():
    assert mgr.transition(ticket("todo"), "cancel", today=TODAY, now=NOW)["status"] == "cancelled"
    reopened = mgr.transition(ticket("done"), "reopen", today=TODAY, now=NOW)
    assert reopened["status"] == "todo" and reopened["done_at"] is None


@pytest.mark.parametrize("status,action", [("doing", "start"), ("done", "done"), ("cancelled", "wait"), ("done", "cancel"), ("todo", "reopen"), ("done", "todo"), ("todo", "fly")])
def test_moves_that_make_no_sense_are_refused(status, action):
    with pytest.raises(mgr.InvalidTicket):
        mgr.transition(ticket(status), action, today=TODAY, now=NOW)


def test_a_follow_up_date_in_the_past_is_refused():
    with pytest.raises(mgr.InvalidTicket, match="ผ่านมาแล้ว"):
        mgr.transition(ticket("todo"), "wait", {"remind_on": "2026-10-20"}, today=TODAY, now=NOW)


def test_dragging_to_a_column_picks_the_right_action():
    assert mgr.move_action(ticket("todo"), "doing") == "start" and mgr.move_action(ticket("waiting"), "doing") == "resume"
    assert mgr.move_action(ticket("doing"), "waiting") == "wait" and mgr.move_action(ticket("doing"), "done") == "done"
    assert mgr.move_action(ticket("doing"), "todo") == "todo" and mgr.move_action(ticket("done"), "todo") == "reopen"
    with pytest.raises(mgr.InvalidTicket):
        mgr.move_action(ticket("todo"), "nowhere")


# ------------------------------------------------------- settings and types
def test_settings():
    assert mgr.clean_settings({"digest_time": "7:05", "digest_enabled": 0, "junk": 1}) == {"digest_time": "07:05", "digest_enabled": False}
    with pytest.raises(mgr.InvalidTicket):
        mgr.clean_settings({"digest_time": "25:00"})
    with pytest.raises(mgr.InvalidTicket):
        mgr.clean_settings({"digest_time": ""})


def test_types():
    assert mgr.clean_type({"label": " งานใหม่ ", "color": "#ABCDEF"}) == {"label": "งานใหม่", "color": "#abcdef"}
    for bad in ({"label": ""}, {"label": "x", "color": "red"}):
        with pytest.raises(mgr.InvalidTicket):
            mgr.clean_type(bad)
    assert mgr.clean_type({"active": False}, existing={"label": "x"}) == {"active": False}


# -------------------------------------------------------------- recurring
def test_a_recurring_template_is_validated():
    v = mgr.clean_recurring({"title": "ตรวจเดินโรงงาน", "type_key": "other", "time_of_day": "9:00", "lead_days": "1", "rule": {"unit": "week", "weekdays": [0]}}, type_keys=TYPES)
    assert v["title"] == "ตรวจเดินโรงงาน" and v["time_of_day"] == "09:00" and v["lead_days"] == 1 and v["mode"] == "calendar"
    assert v["rule"] == {"every": 1, "unit": "week", "weekdays": [0]}
    for bad in ({"title": "", "rule": {"unit": "day"}}, {"title": "x"}, {"title": "x", "rule": {"unit": "day"}, "lead_days": 99},
                {"title": "x", "rule": {"unit": "day"}, "mode": "weird"}, {"title": "x", "rule": {"unit": "day"}, "start_date": "2026-10-02", "end_date": "2026-10-01"}):
        with pytest.raises(mgr.InvalidTicket):
            mgr.clean_recurring(bad, type_keys=TYPES)


# ---------------------------------------------------------- the type by AI
KEYS = [{"key": k, "label": k} for k in ("chase", "finance", "fix", "external", "customer", "purchase", "people", "visit", "decide", "shopping", "report", "personal", "other")]


@pytest.mark.parametrize("text,key", [
    ("โทรตามอะไหล่ที่ชมรม ยังไม่ส่ง", "chase"), ("โอนเงินค่าไฟ", "finance"), ("ปั๊มน้ำเสีย ต้องซ่อม", "fix"), ("ต่อภาษีรถ พรบ", "external"),
    ("ตอบลูกค้าเรื่องราคา", "customer"), ("ขอใบเสนอราคา", "purchase"), ("ประชุมพนักงาน", "people"), ("เดินตรวจ GMP", "visit"),
    ("ตัดสินใจเรื่องโครงการใหม่", "decide"), ("รายงานแม่เรื่องบัญชี", "report"), ("ไปหาหมอ", "personal"), ("zzz", "other"),
])
def test_the_keyword_fallback(text, key):
    assert mgr_ai.keyword_type(text, [k["key"] for k in KEYS]) == key


def test_without_an_api_key_the_rule_decides():
    assert mgr_ai.classify("โอนเงินค่าไฟ", None, KEYS) == ("finance", "rule")


def test_with_an_api_key_the_ai_decides_and_a_failure_falls_back(monkeypatch):
    from app.config import settings
    object.__setattr__(settings, "anthropic_api_key", "k")
    monkeypatch.setattr(mgr_ai, "_ask", lambda title, notes, types: "customer")
    assert mgr_ai.classify("อะไรก็ได้", None, KEYS) == ("customer", "ai")

    def boom(*a):
        raise RuntimeError("down")
    monkeypatch.setattr(mgr_ai, "_ask", boom)
    assert mgr_ai.classify("โอนเงินค่าไฟ", None, KEYS) == ("finance", "rule")          # never blocks on the AI


# ------------------------------------------------------------------ places
def test_the_kind_of_a_place_is_checked():
    assert mgr._place_kind({"kind": "shop"}) == "shop" and mgr._place_kind({}) is None and mgr._place_kind({"kind": ""}) is None
    with pytest.raises(mgr.InvalidTicket, match="ชนิด"):
        mgr._place_kind({"kind": "moon"})
    assert mgr.PLACE_KINDS == ("branch", "shop", "other")
