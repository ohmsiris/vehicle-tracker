"""The drop box engine (app/inbox.py): which reader claims a file, the status a preview gives, booking that replaces (never doubles),
superseding, rebook, the AI job, the checklist turning done from booked data, the routes and who may call them. Readers, the AI and
the checks are FAKES; supabase is a dict in memory -- nothing here reaches the live database."""
import re
import types

import pytest
from fastapi.testclient import TestClient

from app import accounting as acc
from app import inbox
from app import inbox_common as ic
from app import supabase, users


# ------------------------------------------------------------------ fakes
def fake_reader(kind, score, preview=None, *, name=None, book_result=None, log=None, rebook=None):
    m = types.ModuleType(name or f"app.inbox_r_{kind}")
    m.KIND, m.LABEL, m.FEEDS = kind, f"ไฟล์ {kind}", (kind,)
    log = log if log is not None else []

    def sniff(f):
        return score(f) if callable(score) else score

    def read(f):
        p = preview(f) if callable(preview) else dict(preview or {})
        return ic.empty_preview(kind, m.LABEL, **p)

    def book(p, file_id):
        log.append(("book", file_id, [b["key"] for b in p["batches"]]))
        return book_result or {"batches": [b["key"] for b in p["batches"]], "booked": sum(b.get("rows_total") or 0 for b in p["batches"]),
                               "rebook": rebook or []}

    def unbook(p):
        log.append(("unbook", [b["key"] for b in p["batches"]]))
    m.sniff, m.read, m.book, m.unbook, m.log = sniff, read, book, unbook, log
    return m


def batch(key, feed="hq_cheques", branch="สระบุรี", month="2026-11", total=100.0):
    return {"key": key, "feed": feed, "source": feed, "branch": branch, "month": month, "title": key,
            "rows": [{"date": "2026-11-01", "text": "x", "amount": total, "role": "cost", "account": "5300", "flag": None}],
            "file_total": total, "rows_total": total, "entries": []}


def dropped(name="a.pdf", data=b"%PDF-1.4 hello", text="", pages=1, is_scan=False):
    return ic.Dropped(name=name, data=data, ext=".pdf", text=text, pages=pages, is_scan=is_scan)


class FakeDB:
    """inbox_files + the private bucket, in memory. Understands the PostgREST filters the engine sends."""

    def __init__(self):
        self.rows, self.files, self.calls = {}, {}, []
        self.clock = 0

    @staticmethod
    def _match(row, params):
        for k, v in params.items():
            if k in ("select", "order", "limit", "offset"):
                continue
            if k == "or":
                parts = v.strip("()").split(",")
                if not any(FakeDB._match(row, {p.split(".", 1)[0]: p.split(".", 1)[1]}) for p in parts):
                    return False
                continue
            op, _, val = v.partition(".")
            have = row.get(k)
            if op == "eq" and str(have) != val:
                return False
            if op == "neq" and str(have) == val:
                return False
            if op == "in" and str(have) not in val.strip("()").split(","):
                return False
            if op == "ov" and not set(have or []) & {x.strip('"') for x in val.strip("{}").split(",")}:
                return False
        return True

    def select(self, table, params=None):
        assert table == "inbox_files"
        rows = [dict(r) for r in self.rows.values() if self._match(r, params or {})]
        rows.sort(key=lambda r: r["_t"], reverse="desc" in (params or {}).get("order", ""))
        return rows[: int(params["limit"])] if params and params.get("limit") else rows

    def select_all(self, table, params, page=1000):
        return self.select(table, params)

    def insert(self, table, rows, **kw):
        out = []
        for r in rows:
            if any(x["sha256"] == r["sha256"] for x in self.rows.values()):
                raise supabase.SupabaseError("duplicate key")
            self.clock += 1
            row = {"status": "read", "batches": [], "choices": {}, "branches": [], "uploaded_at": f"2026-11-24T00:00:{self.clock:02d}", **r, "_t": self.clock}
            self.rows[row["id"]] = row
            out.append(dict(row))
        return out

    def update(self, table, match, values):
        out = []
        for r in self.rows.values():
            if self._match(r, match):
                r.update(values)
                out.append(dict(r))
        return out

    def delete(self, table, match):
        self.calls.append(("delete", table, match))
        for k in [k for k, r in self.rows.items() if self._match(r, match)]:
            del self.rows[k]

    def storage_upload(self, path, data, content_type, upsert=False, bucket=supabase.STORAGE_BUCKET):
        assert bucket == supabase.PRIVATE_BUCKET and path.startswith("inbox/")
        self.files[path] = data

    def storage_download(self, path, bucket=supabase.STORAGE_BUCKET):
        assert bucket == supabase.PRIVATE_BUCKET
        return self.files[path]

    def storage_delete(self, paths, bucket=supabase.STORAGE_BUCKET):
        assert bucket == supabase.PRIVATE_BUCKET
        for p in paths:
            self.files.pop(p, None)


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    for name in ("select", "select_all", "insert", "update", "delete", "storage_upload", "storage_download", "storage_delete"):
        monkeypatch.setattr(supabase, name, getattr(d, name))
    monkeypatch.setattr(supabase, "configured", lambda: True)
    return d


@pytest.fixture
def readers(monkeypatch):
    """The installed readers, settable per test."""
    box: list = []
    monkeypatch.setattr(inbox, "_readers", lambda: box)
    return box


# ------------------------------------------------------------------ which reader claims a file
def test_the_most_sure_reader_wins_and_a_weak_score_goes_to_the_scan_reader():
    cheq, pea = fake_reader("cheques", 0.9), fake_reader("pea_bill", 0.6)
    scan = fake_reader("scan", 0.0, name="app.inbox_r_scan")
    assert inbox.choose_reader(dropped(), [pea, cheq, scan]) == (cheq, 0.9)
    weak = fake_reader("cheques", ic.MIN_SNIFF - 0.01)
    assert inbox.choose_reader(dropped(), [weak, scan]) == (scan, 0.0)
    assert inbox.choose_reader(dropped(), [weak]) == (None, 0.0)          # no scan reader installed yet


def test_a_tie_goes_to_the_first_reader_and_a_crashing_sniff_claims_nothing():
    a, b = fake_reader("a", 0.8), fake_reader("b", 0.8)
    assert inbox.choose_reader(dropped(), [a, b])[0] is a

    def boom(f):
        raise RuntimeError("bad file")
    assert inbox.choose_reader(dropped(), [fake_reader("x", boom), b])[0] is b


def test_the_name_alone_never_decides():
    """A reader looks at the content; one that only likes the name scores low and the file goes to the scan reader."""
    by_content = fake_reader("cheques", lambda f: 0.9 if "เช็ค" in f.text else 0.1)
    scan = fake_reader("scan", 0, name="app.inbox_r_scan")
    assert inbox.choose_reader(dropped(name="เช็ค พ.ย.pdf", text="ใบแจ้งค่าไฟฟ้า"), [by_content, scan])[0] is scan


def test_unknown_files_are_kept_and_offered_to_the_ai_with_their_page_count():
    kind, p, err = inbox.read_file("scan.pdf", b"%PDF-1.4", {}, readers=[])
    assert kind == "unknown" and p["needs_ai"] and err is None and inbox.status_of(p) == "needs_ai"


def test_a_reader_that_crashes_marks_the_file_failed_with_a_thai_message():
    def bad(f):
        raise ValueError("ไม่พบหัวตาราง")
    kind, p, err = inbox.read_file("a.xlsx", b"PK..", {}, readers=[fake_reader("cheques", 0.9, bad)])
    assert kind == "cheques" and err.startswith("อ่านไฟล์ไม่สำเร็จ") and "ไม่พบหัวตาราง" in err
    assert inbox.file_values(kind, p, err)["status"] == "failed"


# ------------------------------------------------------------------ status, values
@pytest.mark.parametrize("preview,status", [
    ({"conflicts": [{"key": "month"}], "store_only": True}, "needs_choice"),     # a conflict wins over everything
    ({"store_only": True}, "stored"),
    ({"needs_ai": True}, "needs_ai"),
    ({"batches": [batch("k")]}, "read"),
])
def test_status_from_the_preview(preview, status):
    assert inbox.status_of(ic.empty_preview("x", "x", **preview)) == status


def test_file_values_take_the_month_and_branches_from_the_preview():
    p = ic.empty_preview("cheques", "x", month="2026-11", batches=[batch("a", branch="แก่งคอย"), batch("b"), batch("c", branch="สระบุรี")])
    v = inbox.file_values("cheques", p, None)
    assert v["month"] == "2026-11" and v["branches"] == sorted(["สระบุรี", "แก่งคอย"])
    p2 = ic.empty_preview("payroll", "x", signals=[{"what": "branch", "source": "หัวกระดาษ", "value": "แก่งคอย"}])
    assert inbox.file_values("payroll", p2, None)["branches"] == ["แก่งคอย"]


def test_choices_must_answer_an_open_conflict_with_one_of_its_options():
    p = {"conflicts": [{"key": "month", "options": [{"value": "2026-10"}, {"value": "2026-11"}]}]}
    assert inbox.clean_choices(p, {}, {"month": "2026-11"}) == {"month": "2026-11"}
    with pytest.raises(inbox.InboxError):
        inbox.clean_choices(p, {}, {"month": "2026-07"})
    with pytest.raises(inbox.InboxError):
        inbox.clean_choices(p, {}, {})
    with pytest.raises(inbox.InboxError):
        inbox.clean_choices({"conflicts": []}, {}, {"branch": "x"})              # nothing to choose any more
    assert inbox.clean_choices({"conflicts": []}, {"month": "2026-10"}, {"month": "2026-11"}) == {"month": "2026-11"}   # change an earlier answer


def test_an_ai_preview_takes_the_month_answer_without_reading_again():
    p = ic.empty_preview("scan", "x", conflicts=[{"key": "month"}], batches=[batch("a", month="2026-10")])
    out = inbox.apply_choices(p, {"month": "2026-11"})
    assert out["conflicts"] == [] and out["month"] == "2026-11" and out["batches"][0]["month"] == "2026-11"


# ------------------------------------------------------------------ superseding and rebook (pure)
def test_the_newest_booking_owns_a_batch_key():
    rows = [{"id": "old", "status": "booked", "batches": ["k1", "k2"]}, {"id": "other", "status": "booked", "batches": ["z"]},
            {"id": "notbooked", "status": "read", "batches": ["k1"]}, {"id": "me", "status": "booked", "batches": ["k1"]}]
    assert inbox.superseded(rows, ["k1"], "me") == [("old", ["k2"])]
    assert inbox.superseded(rows, ["k1", "k2"], "me") == [("old", [])]


def test_rebook_picks_booked_files_of_that_kind_and_month_once():
    rows = [{"id": "1", "kind": "cheques", "month": "2026-11", "status": "booked"},
            {"id": "2", "kind": "cheques", "month": "2026-10", "status": "booked"},
            {"id": "3", "kind": "cheques", "month": "2026-11", "status": "read"},
            {"id": "4", "kind": "pea_bill", "month": "2026-11", "status": "booked"},
            {"id": "5", "kind": "cheques", "month": "2026-11", "status": "booked"}]
    got = inbox.rebook_targets([{"kind": "cheques", "month": "2026-11"}, {"kind": "cheques", "month": "2026-11"}], rows, {"5"})
    assert [r["id"] for r in got] == ["1"]


def test_row_view_sums_the_batches_and_a_dead_ai_job_offers_the_button_again():
    row = {"id": "x", "file_name": "a.pdf", "status": "ai_running", "batches": ["a"],
           "preview": ic.empty_preview("cheques", "เช็ค", batches=[batch("a", total=10), batch("b", total=5.5)])}
    v = inbox.row_view(row, running=set())
    assert v["status"] == "failed" and "AI" in v["error"] and v["file_total"] == 15.5 and v["rows_total"] == 15.5
    assert [b["booked"] for b in v["batches"]] == [True, False]
    assert inbox.row_view(row, running={"x"})["status"] == "ai_running"


def test_files_by_feed_says_which_file_filled_a_row_and_whether_it_is_booked():
    rows = [{"id": "1", "file_name": "เช็ค.xls", "status": "booked", "batches": ["c-s"],
             "preview": {"batches": [batch("c-s"), batch("c-k", branch="แก่งคอย")]}},
            {"id": "2", "file_name": "PEA.pdf", "status": "read", "batches": [], "preview": {"batches": [batch("p", feed="electricity")]}},
            {"id": "3", "file_name": "scan.pdf", "status": "needs_ai", "batches": [], "preview": {"batches": []}}]
    both = inbox.files_by_feed(rows)
    assert both["hq_cheques"] == [{"id": "1", "file_name": "เช็ค.xls", "status": "booked", "booked": True}]
    assert both["electricity"][0]["booked"] is False
    kk = inbox.files_by_feed(rows, "แก่งคอย")
    assert kk["hq_cheques"][0]["booked"] is False                     # the Kaeng Khoi batch of that file is not booked


# ------------------------------------------------------------------ the checklist (phase 3)
def test_the_fuel_card_and_salaries_rows_tick_from_their_booked_sources():
    rows = {f["key"]: f for f in acc.feed_status(2, {"card_fuel": (3, 45_000.0), "payroll": (2, 300_000.0)}, {})}
    assert rows["fuel_card"]["state"] == "done" and rows["fuel_card"]["total"] == 45_000.0
    assert rows["hq_salary"]["state"] == "done" and rows["hq_salary"]["entries"] == 2
    assert acc.feed_sources("electricity") == ("electricity",)


def test_a_row_is_done_from_a_booked_dropped_file_and_received_from_one_not_booked_yet():
    d = {"hq_cheques": [{"id": "1", "file_name": "เช็ค.xls", "status": "booked", "booked": True}],
         "electricity": [{"id": "2", "file_name": "PEA.pdf", "status": "read", "booked": False}]}
    rows = {f["key"]: f for f in acc.feed_status(2, {}, {}, 0, {}, d)}
    assert rows["hq_cheques"]["state"] == "done" and rows["hq_cheques"]["dropped"][0]["file_name"] == "เช็ค.xls"
    assert rows["electricity"]["state"] == "received"
    assert rows["water_city"]["state"] == "missing" and rows["water_city"]["dropped"] == []


# ------------------------------------------------------------------ the engine against the fake database
def cheque_reader(log, month="2026-11", conflicts=None, rebook=None):
    def preview(f):
        m = f.choices.get("month") or month
        return {"month": m, "batches": [batch(f"cheque-{m}-สระบุรี", month=m, total=845_000.0)],
                "conflicts": [] if f.choices.get("month") else list(conflicts or []),
                "signals": [{"what": "month", "source": "วันที่บนเช็ค", "value": m}]}
    return fake_reader("cheques", lambda f: 0.0 if f.is_scan else 0.9, preview, log=log, rebook=rebook)


def test_upload_stores_privately_reads_and_never_keeps_the_same_file_twice(db, readers):
    log = []
    readers.append(cheque_reader(log))
    out = inbox.upload([("เช็ค พ.ย.xls", b"sheet-bytes"), ("copy.xls", b"sheet-bytes"), ("empty.pdf", b"")], "เจ้าของ")
    assert out[0]["status"] == "read" and out[0]["kind"] == "cheques" and out[0]["month"] == "2026-11" and out[0]["branches"] == ["สระบุรี"]
    assert "ไม่เพิ่มซ้ำ" in out[1]["message"] and out[1]["id"] == out[0]["id"]
    assert out[2]["status"] == "failed" and "ไฟล์ว่าง" in out[2]["message"]
    assert len(db.rows) == 1 and list(db.files) == [f"inbox/{out[0]['id']}.xls"]
    again = inbox.upload([("renamed.xls", b"sheet-bytes")], "office")
    assert again[0]["id"] == out[0]["id"] and "อยู่ในกล่องแล้ว" in again[0]["message"] and len(db.rows) == 1
    assert log == []                                                  # nothing is booked on upload


def test_booking_replaces_and_a_conflict_must_be_answered_first(db, readers):
    log = []
    conflict = [{"key": "month", "message": "หัวกระดาษบอก ต.ค. 2569 แต่วันที่บนเช็คเป็น พ.ย. 2569",
                 "options": [{"value": "2026-11", "label": "พ.ย."}, {"value": "2026-10", "label": "ต.ค."}], "default": "2026-11"}]
    readers.append(cheque_reader(log, conflicts=conflict))
    fid = inbox.upload([("เช็ค.xls", b"x1")])[0]["id"]
    assert db.rows[fid]["status"] == "needs_choice"
    with pytest.raises(inbox.InboxError, match="ต้องเลือก"):
        inbox.book(fid)
    v = inbox.choose(fid, {"month": "2026-10"})
    assert v["status"] == "read" and v["month"] == "2026-10" and db.rows[fid]["choices"] == {"month": "2026-10"}
    v = inbox.book(fid)
    assert v["status"] == "booked" and v["booked_total"] == 845_000.0 and db.rows[fid]["batches"] == ["cheque-2026-10-สระบุรี"]
    # changing the answer on a booked file: what it booked is taken back, then booked again under the new key
    inbox.choose(fid, {"month": "2026-11"})
    assert log[-2:] == [("unbook", ["cheque-2026-10-สระบุรี"]), ("book", fid, ["cheque-2026-11-สระบุรี"])]
    assert db.rows[fid]["status"] == "booked" and db.rows[fid]["batches"] == ["cheque-2026-11-สระบุรี"]


def test_a_newer_file_takes_over_the_key_so_removing_the_old_one_deletes_nothing_of_it(db, readers):
    log = []
    readers.append(cheque_reader(log))
    a = inbox.upload([("เช็ค v1.xls", b"v1")])[0]["id"]
    b = inbox.upload([("เช็ค v2.xls", b"v2")])[0]["id"]
    inbox.book(a)
    inbox.book(b)
    assert db.rows[a]["status"] == "read" and db.rows[a]["batches"] == [] and "แทนที่" in db.rows[a]["error"]
    log.clear()
    inbox.remove(a)
    assert log == [] and a not in db.rows and f"inbox/{a}.xls" not in db.files        # nothing unbooked: v2 owns the key
    inbox.remove(b)
    assert log == [("unbook", ["cheque-2026-11-สระบุรี"])] and not db.rows and not db.files


def test_rebook_re_reads_and_re_books_the_booked_files_it_names(db, readers):
    log = []
    readers.append(cheque_reader(log))
    readers.append(fake_reader("pea_bill", lambda f: 0.95 if f.data.startswith(b"PEA") else 0,
                               {"month": "2026-11", "batches": [batch("pea-1", feed="electricity")]}, log=log,
                               rebook=[{"kind": "cheques", "month": "2026-11"}]))
    c = inbox.upload([("เช็ค.xls", b"cheque")])[0]["id"]
    inbox.book(c)
    p = inbox.upload([("PEA.pdf", b"PEA bill")])[0]["id"]
    log.clear()
    v = inbox.book(p)
    assert v["rebooked"] == 1
    assert log == [("book", p, ["pea-1"]), ("unbook", ["cheque-2026-11-สระบุรี"]), ("book", c, ["cheque-2026-11-สระบุรี"])]


def test_store_only_needs_ai_and_failed_files_cannot_be_booked(db, readers):
    readers.append(fake_reader("payslip", lambda f: 0.9 if f.data == b"slip" else 0, {"store_only": True, "note": "สลิปเงินเดือน"}))
    s = inbox.upload([("slip.pdf", b"slip")])[0]
    assert s["status"] == "stored"
    with pytest.raises(inbox.InboxError, match="ไม่ลงบัญชี"):
        inbox.book(s["id"])
    u = inbox.upload([("unknown.pdf", b"%PDF-1.4 nothing")])[0]
    assert u["status"] == "needs_ai"
    with pytest.raises(inbox.InboxError, match="AI"):
        inbox.book(u["id"])
    with pytest.raises(inbox.NotFound):
        inbox.book("not-a-uuid")


def test_a_failed_reread_keeps_what_is_booked(db, readers):
    log = []
    r = cheque_reader(log)
    readers.append(r)
    fid = inbox.upload([("เช็ค.xls", b"x")])[0]["id"]
    inbox.book(fid)

    def broken(f):
        raise ValueError("รูปแบบไฟล์เปลี่ยน")
    r.read = lambda f: broken(f)
    with pytest.raises(inbox.InboxError, match="ยังอยู่เหมือนเดิม"):
        inbox.reread(fid)
    assert db.rows[fid]["status"] == "booked" and ("unbook", ["cheque-2026-11-สระบุรี"]) not in log


def test_list_is_the_month_plus_everything_not_booked_newest_first(db, readers):
    log = []
    readers.append(cheque_reader(log, month="2026-10"))
    old = inbox.upload([("ต.ค.xls", b"oct")])[0]["id"]
    inbox.book(old)
    readers[0] = cheque_reader(log, month="2026-11")
    new = inbox.upload([("พ.ย.xls", b"nov")])[0]["id"]
    assert [f["id"] for f in inbox.list_files("2026-11")["files"]] == [new]
    assert [f["id"] for f in inbox.list_files("2026-10")["files"]] == [new, old]
    with pytest.raises(inbox.InboxError):
        inbox.list_files("พ.ย.")


def test_the_ai_reads_in_the_background_only_when_allowed(db, readers, monkeypatch):
    from app.config import settings
    fid = inbox.upload([("scan.pdf", b"%PDF-1.4 scan")])[0]["id"]
    with pytest.raises(inbox.InboxError, match="ANTHROPIC"):
        inbox.ai(fid)
    object.__setattr__(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(inbox, "_optional", lambda name: None)
    with pytest.raises(inbox.InboxError, match="ยังไม่พร้อม"):
        inbox.ai(fid)
    seen = {}

    def run(row, data, on_progress):
        seen["status"] = db.rows[row["id"]]["status"]
        on_progress(1, 2)
        return ic.empty_preview("scan", "เอกสารลายมือ", month="2026-11", batches=[batch("mid-2026-11", feed="midmonth")],
                                ai_usage={"model": "m", "calls": 2, "input_tokens": 900, "output_tokens": 120})
    ai_mod = types.SimpleNamespace(run=run)
    monkeypatch.setattr(inbox, "_optional", lambda name: ai_mod if name == "inbox_ai" else None)
    monkeypatch.setattr(inbox, "_spawn", lambda fn, *a: fn(*a))           # run the job here, not in a thread
    inbox.ai(fid)
    row = db.rows[fid]
    assert seen["status"] == "ai_running" and row["status"] == "read" and row["ai_usage"]["input_tokens"] == 900 and not inbox._jobs
    with pytest.raises(inbox.InboxError, match="ไม่ต้องใช้ AI"):
        inbox.ai(fid)
    # an AI-read file books with the default entries-only booking (there is no exact reader for its kind)
    monkeypatch.setattr(ic, "book_batches", lambda p: {"batches": [b["key"] for b in p["batches"]], "booked": 1.0, "rebook": []})
    assert inbox.book(fid)["status"] == "booked"


def test_an_ai_failure_is_recorded_in_thai(db, readers, monkeypatch):
    from app.config import settings
    object.__setattr__(settings, "anthropic_api_key", "test-key")
    fid = inbox.upload([("scan.pdf", b"%PDF-1.4 scan")])[0]["id"]

    def run(row, data, on_progress):
        raise RuntimeError("overloaded")
    monkeypatch.setattr(inbox, "_optional", lambda name: types.SimpleNamespace(run=run))
    monkeypatch.setattr(inbox, "_spawn", lambda fn, *a: fn(*a))
    inbox.ai(fid)
    assert db.rows[fid]["status"] == "failed" and db.rows[fid]["error"].startswith("AI อ่านไม่สำเร็จ")


def test_checks_are_a_placeholder_until_acc_checks_exists(monkeypatch):
    monkeypatch.setattr(inbox, "_optional", lambda name: None)
    assert inbox.checks("2026-11")["available"] is False
    fake = types.SimpleNamespace(run=lambda m, b: {"checks": [{"key": "twice", "title": "ไม่นับซ้ำ", "state": "ok", "summary": "", "numbers": {}, "rows": []}]})
    monkeypatch.setattr(inbox, "_optional", lambda name: fake)
    assert inbox.checks("2026-11", "สระบุรี")["checks"][0]["key"] == "twice"
    with pytest.raises(inbox.InboxError):
        inbox.checks("bad")


# ------------------------------------------------------------------ routes and permissions
def _client(monkeypatch):
    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    return TestClient(main.app)


def test_routes_upload_list_book_and_map_errors_to_thai_4xx(db, readers, monkeypatch):
    log = []
    readers.append(cheque_reader(log))
    c = _client(monkeypatch)
    h = {"X-App-Token": "secret"}
    r = c.post("/api/accounts/inbox", headers=h, files=[("files", ("เช็ค.xls", b"abc", "application/vnd.ms-excel")),
                                                          ("files", ("scan.jpg", b"\xff\xd8\xff\xe0", "image/jpeg"))])
    assert r.status_code == 200, r.text
    got = r.json()["files"]
    assert [f["status"] for f in got] == ["read", "needs_ai"]
    fid = got[0]["id"]
    assert db.rows[fid]["uploaded_by"] == "เจ้าของ"
    lst = c.get("/api/accounts/inbox?month=2026-11", headers=h).json()
    assert {f["id"] for f in lst["files"]} == {fid, got[1]["id"]} and lst["ai_ready"] is False
    one = c.get(f"/api/accounts/inbox/{fid}", headers=h).json()
    assert one["preview"]["batches"][0]["rows"][0]["role"] == "cost" and one["roles"]["funding"].startswith("เติมเงินสดย่อย")
    f = c.get(f"/api/accounts/inbox/{fid}/file?token=secret")
    assert f.status_code == 200 and f.content == b"abc"
    assert c.post(f"/api/accounts/inbox/{got[1]['id']}/book", headers=h).status_code == 422
    assert c.post(f"/api/accounts/inbox/{fid}/book", headers=h).json()["status"] == "booked"
    assert c.post(f"/api/accounts/inbox/{fid}/choose", headers=h, json={"choices": {"month": "2026-07"}}).status_code == 422
    assert c.post("/api/accounts/inbox/00000000-0000-0000-0000-000000000000/reread", headers=h).status_code == 404
    assert c.post(f"/api/accounts/inbox/{got[1]['id']}/ai", headers=h).status_code == 422          # no AI key on the server
    assert c.delete(f"/api/accounts/inbox/{fid}", headers=h).json() == {"ok": True} and fid not in db.rows
    assert c.get("/api/accounts/checks?month=2026-11", headers=h).json()["available"] in (False, True)
    assert c.get("/api/accounts/inbox", headers={"X-App-Token": "wrong"}).status_code == 403


def test_only_the_inputs_menu_can_use_the_drop_box():
    for path in ("/api/accounts/inbox", "/api/accounts/inbox/x/book", "/api/accounts/inbox/x/ai"):
        assert users.allowed(["accounts_in"], path)
        assert not users.allowed(["accounts"], path)
    for path in ("/api/accounts/report", "/api/accounts/year", "/api/accounts/unit-cost", "/api/accounts/checks", "/api/accounts/docs/x/image"):
        assert users.allowed(["accounts"], path) and users.allowed(["accounts_in"], path)


def test_every_accounts_path_the_pages_call_is_covered():
    """Nothing the P&L page calls was lost when its menu stopped covering every /api/accounts/ path."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "static"
    calls = set()
    for name in ("accounts.js", "unitcost.js"):
        calls |= set(re.findall(r"/api/accounts/[a-z-]+", (root / name).read_text(encoding="utf-8")))
    for p in calls - {"/api/accounts/inbox"}:
        assert users.allowed(["accounts"], p), p
