from datetime import date, datetime

import httpx
import pytest

from app import line_push, mgr_jobs as j
from app.config import settings

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


class Fakes:
    def __init__(self, tickets, settings=None, fail=False):
        self.tickets, self.sent, self.claimed, self.fail = tickets, [], set(), fail
        self.settings = settings or {"digest_time": "08:00", "digest_enabled": True, "timed_enabled": True}

    def claim(self, key):
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    def release(self, key):
        self.claimed.discard(key)

    def push(self, text):
        if self.fail:
            raise line_push.LinePushError("down")
        self.sent.append(text)

    def tick(self, now):
        return j.run_tick(now, settings=self.settings, active_tickets=lambda: self.tickets, claim=self.claim, release=self.release, push=self.push)


def test_the_digest_goes_out_once_a_day_and_not_before_its_time():
    f = Fakes([t(1, "วันนี้", due_date="2026-10-24")])
    assert f.tick(datetime(2026, 10, 24, 7, 59))["digest"] is False
    assert f.tick(datetime(2026, 10, 24, 8, 0))["digest"] is True
    assert f.tick(datetime(2026, 10, 24, 8, 1))["digest"] is False and f.tick(datetime(2026, 10, 24, 20, 0))["digest"] is False
    assert len(f.sent) == 1
    assert f.tick(datetime(2026, 10, 25, 8, 0))["digest"] is True              # tomorrow it is overdue: a new day, a new summary
    assert "เลยกำหนด (1)" in f.sent[1]


def test_a_failed_push_is_retried_not_lost():
    f = Fakes([t(1, "วันนี้", due_date="2026-10-24")], fail=True)
    assert f.tick(datetime(2026, 10, 24, 8, 0))["digest"] is False and f.claimed == set()
    f.fail = False
    assert f.tick(datetime(2026, 10, 24, 8, 1))["digest"] is True


def test_timed_reminders_go_out_once_each():
    f = Fakes([t(1, "โทรหาธนาคาร", due_date="2026-10-24", due_time="14:00")], settings={"digest_time": "08:00", "digest_enabled": False, "timed_enabled": True})
    assert f.tick(datetime(2026, 10, 24, 14, 0))["timed"] == 1
    assert f.tick(datetime(2026, 10, 24, 14, 1))["timed"] == 0
    assert f.sent == ["ถึงเวลา 14:00: โทรหาธนาคาร"]


def test_both_can_be_switched_off():
    f = Fakes([t(1, "x", due_date="2026-10-24", due_time="08:00")], settings={"digest_enabled": False, "timed_enabled": False})
    assert f.tick(datetime(2026, 10, 24, 12, 0)) == {"digest": False, "timed": 0} and f.sent == []


# ------------------------------------------------------------------ LINE
def test_line_push_is_plain_text_to_the_owner(monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"], seen["body"] = request.headers["authorization"], request.read()
        return httpx.Response(200, json={})
    object.__setattr__(settings, "line_channel_access_token", "tok")
    object.__setattr__(settings, "owner_line_user_id", "Uabc")
    assert line_push.configured()
    line_push.push_text("สวัสดี", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen["auth"] == "Bearer tok" and b'"to":"Uabc"' in seen["body"] and b'"type":"text"' in seen["body"]


def test_line_push_errors_are_reported():
    object.__setattr__(settings, "line_channel_access_token", "tok")
    object.__setattr__(settings, "owner_line_user_id", "Uabc")
    with pytest.raises(line_push.LinePushError, match="401"):
        line_push.push_text("x", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))))
    object.__setattr__(settings, "line_channel_access_token", "")
    assert not line_push.configured()
    with pytest.raises(line_push.LinePushError, match="ยังไม่ได้ตั้งค่า"):
        line_push.push_text("x")
