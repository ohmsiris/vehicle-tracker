from datetime import date, datetime

from app import mgr_jobs as j

TODAY = date(2026, 10, 24)


def t(i, title, **kw):
    return {"id": f"t{i}", "seq": i, "title": title, "status": "todo", "urgent": False, "due_date": None, "due_time": None,
            "remind_on": None, "place": None, "waiting_on": None, **kw}


def test_nothing_to_say_means_no_digest():
    assert j.build_digest([], TODAY) is None
    assert j.build_digest([t(1, "ไกลๆ", due_date="2026-11-30"), t(2, "ไม่มีวัน")], TODAY) is None


def test_the_digest_lists_overdue_today_follow_ups_and_urgent():
    tickets = [
        t(1, "จ่ายค่าไฟ", due_date="2026-10-21", urgent=True),
        t(2, "โทรตามอะไหล่", due_date="2026-10-24", due_time="10:00:00", place="สระบุรี"),
        t(3, "รอชมรม", status="waiting", waiting_on="ชมรมย์การช่าง", remind_on="2026-10-24"),
        t(4, "ตรวจเดินโรงงาน", urgent=True),
        t(5, "กำลังทำ", status="doing"),
        t(6, "ปิดแล้ว", status="done", due_date="2026-10-20"),
    ]
    text = j.build_digest(tickets, TODAY)
    assert text.splitlines()[0] == "สรุปงานวันนี้ · 24 ต.ค. 2569"
    assert "เลยกำหนด (1)" in text and "• ❗จ่ายค่าไฟ (เลย 3 วัน)" in text
    assert "วันนี้ (1)" in text and "• โทรตามอะไหล่ (10:00 · สระบุรี)" in text
    assert "ถึงวันติดตาม (1)" in text and "• รอชมรม (รอ ชมรมย์การช่าง)" in text
    assert "ด่วน (1)" in text and "• ❗ตรวจเดินโรงงาน" in text
    assert text.endswith("กำลังทำอยู่ 1 เรื่อง") and "ปิดแล้ว" not in text


def test_a_ticket_is_listed_once_and_long_lists_are_cut():
    many = [t(i, f"งาน {i}", due_date="2026-10-24") for i in range(20)]
    text = j.build_digest(many, TODAY)
    assert text.count("• งาน") == j.MAX_LINES and "และอีก 8 เรื่อง" in text
    one = [t(1, "ด่วนและวันนี้", due_date="2026-10-24", urgent=True)]
    assert j.build_digest(one, TODAY).count("ด่วนและวันนี้") == 1


def test_timed_reminders_are_for_today_when_their_time_has_come():
    tickets = [t(1, "ก่อนเวลา", due_date="2026-10-24", due_time="15:00"), t(2, "ถึงเวลา", due_date="2026-10-24", due_time="14:00:00"),
               t(3, "นานแล้ว", due_date="2026-10-24", due_time="08:00"), t(4, "พรุ่งนี้", due_date="2026-10-25", due_time="09:00"),
               t(5, "เสร็จแล้ว", due_date="2026-10-24", due_time="14:00", status="done")]
    now = datetime(2026, 10, 24, 14, 5)
    assert [x["title"] for x in j.due_timed(tickets, now)] == ["ถึงเวลา"]
    assert j.timed_text(tickets[1]) == "ถึงเวลา 14:00: ถึงเวลา"


# ------------------------------------------------------------ the top bar and the menu numbers
def test_the_numbers_for_the_top_bar():
    tickets = [t(1, "เลย", due_date="2026-10-20"), t(2, "วันนี้", due_date="2026-10-24", due_time="14:00:00", place="โรงงาน"),
               t(3, "ติดตาม", status="waiting", remind_on="2026-10-23"), t(4, "ด่วนล้วน", urgent=True), t(5, "ทำอยู่", status="doing"),
               t(6, "ไกล", due_date="2026-12-01"), t(7, "ปิด", status="done", due_date="2026-10-20")]
    s = j.summarize_tickets(tickets, TODAY)
    assert (s["total"], s["overdue"], s["today"], s["follow_ups"], s["urgent"], s["doing"]) == (6, 1, 1, 1, 1, 1)
    assert s["timed"] == [{"id": "t2", "title": "วันนี้", "time": "14:00", "place": "โรงงาน", "urgent": False}]
    assert s["ready"] and "เลยกำหนด (1)" in s["text"]


def test_a_quiet_day_has_zero_everywhere():
    s = j.summarize_tickets([t(1, "ไกล", due_date="2026-12-01")], TODAY)
    assert (s["overdue"], s["today"], s["follow_ups"], s["urgent"]) == (0, 0, 0, 0) and s["text"] == "" and s["timed"] == []


def test_the_car_side_is_counted_by_stage():
    found = [{"stage": "overdue", "vehicle_id": "v1", "truck": "#1 1ฒถ 8966", "label": "น้ำมันเครื่อง", "days_left": -3, "km_left": -400, "km_real": False, "key": "a"},
             {"stage": "soon", "vehicle_id": "v2", "truck": "#2 x", "label": "น้ำมันเครื่อง", "days_left": 6, "km_left": 900, "km_real": True, "key": "b"},
             {"stage": "soon", "vehicle_id": "v3", "truck": "#3 y", "label": "น้ำมันเครื่อง", "days_left": 9, "km_left": None, "km_real": False, "key": "c"}]
    p = j.summarize_plan(found, True, 10)
    assert p["counts"] == {"overdue": 1, "due": 0, "soon": 2} and p["total"] == 3 and p["soon_days"] == 10 and p["enabled"]
    assert [x["truck"] for x in p["items"]] == ["#1 1ฒถ 8966", "#2 x", "#3 y"]
    many = [{**found[1], "key": str(i)} for i in range(20)]
    assert len(j.summarize_plan(many, True, 10)["items"]) == j.PLAN_ITEMS_SHOWN and j.summarize_plan(many, True, 10)["total"] == 20


def test_there_is_no_line_any_more():
    import importlib.util
    assert importlib.util.find_spec("app.line_push") is None
