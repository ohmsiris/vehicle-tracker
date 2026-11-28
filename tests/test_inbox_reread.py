"""ใบเบิกกลางเดือน's deduction columns (owner, 28 Nov 2026): the page reader's advance_rows (ส่วนกลาง / ความผิด / อุปกรณ์ per person,
app/inbox_ai.py PAGE_SCHEMA), and อ่านหน้านี้ใหม่ -- only some pages of an AI-read file read again from their stored images
(inbox_ai.reread_pages, inbox.reread_pages, POST /api/accounts/inbox/<id>/pages/reread). Fakes only: the model's answers are made up
(no network), supabase is a dict in memory -- never the live database."""
import json
import threading

import pytest

from app import inbox, inbox_ai, supabase, users
from tests.test_inbox import _client, db, fake_reader, readers  # noqa: F401 -- db / readers: the engine's in-memory inbox_files
from tests.test_inbox_ai import MID_PAGES, VEH, FakeClient, ai_on, line, page, use_client  # noqa: F401 -- ai_on: a fixture

FID = "44444444-4444-4444-4444-444444444444"
ADVANCE_OLD = page("advance", title="รายการเบิกกลางเดือน : ช่าง / ออฟฟิศ / ฝ่ายผลิต", written_total=6000,
                   lines=[line("2026-11-08", "นาย ก SB-FR001", 3000, "1500"), line("2026-11-08", "นาย ข SB-FR002", 3000, "1500")])


def adv_row(code, amount, common="", fault="", other="", unsure=False):
    return {"code": code, "name": f"นาย {code[-1]}", "amount": amount, "common": common, "fault": fault, "other": other, "unsure": unsure}


ADVANCE_NEW = {**ADVANCE_OLD, "work_rows": [], "advance_rows": [adv_row("SB-FR001", "3000", common="350"), adv_row("SB-FR002", "3000", common="630", fault="500")]}


def reads():
    """The file as it was read before advance_rows existed: MID_PAGES, then the advance list as page 5."""
    return [{**p, "n": i} for i, p in enumerate(json.loads(json.dumps(MID_PAGES + [ADVANCE_OLD])), 1)]


EDITS = {"edits": {"p2:l1": {"amount": 450}, "p5:l1": {"amount": 3500}}, "added": []}
HR = [{"id": "p4:warning:SB-FR068", "status": "pending", "kind": "warning", "page": 4}]


@pytest.fixture
def engine(monkeypatch, db, readers):
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    monkeypatch.setattr(inbox_ai, "_cash_fuel_lines", lambda m, b, h: [])
    monkeypatch.setattr(inbox_ai, "_earlier_docs", lambda *a: [])
    log: list = []
    readers.append(fake_reader("scan", 0, log=log))
    got: list = []
    monkeypatch.setattr(supabase, "storage_download_private", lambda p: got.append(p) or b"\xff\xd8\xff jpeg")
    return {"book": log, "images": got}


def scan_row(db, status="read", edits=EDITS, **extra):
    pv = inbox_ai.from_pages(reads(), "กลางเดือนพฤศจิกา69 สระบุรี.pdf", {}, line_edits=edits)
    pv = {**pv, "hr_items": HR, "ai_images": {str(i): f"accdocs/inbox/{FID}/p{i}.jpg" for i in range(1, 6)}}
    db.rows[FID] = {"id": FID, "file_name": "กลางเดือนพฤศจิกา69 สระบุรี.pdf", "kind": "scan", "status": status, "storage_path": f"inbox/{FID}.pdf",
                    "mime_type": "application/pdf", "batches": [], "choices": {}, "branches": ["สระบุรี"], "_t": 0, "sha256": "s",
                    "ai_usage": {"model": "m", "calls": 5, "input_tokens": 5000, "output_tokens": 1000}, "month": "2026-11",
                    "preview": json.loads(json.dumps(pv)), **extra}
    return db.rows[FID]


def client_for(monkeypatch, n=5, answer=ADVANCE_NEW):
    pages = [{}] * 4 + [answer] if n == 5 else [answer]
    c = FakeClient(pages)
    use_client(monkeypatch, c)
    return c


# ------------------------------------------------------------------ the schema
def test_the_schema_asks_advance_lists_for_their_deduction_columns():
    s = inbox_ai.PAGE_SCHEMA
    assert "advance_rows" in s["required"] and s["properties"]["advance_rows"]["type"] == "array"
    item = s["properties"]["advance_rows"]["items"]
    assert item["required"] == ["code", "name", "amount", "common", "fault", "other", "unsure"] and item["additionalProperties"] is False
    assert all(item["properties"][k]["type"] == "string" for k in ("code", "name", "amount", "common", "fault", "other"))      # as written, like work_rows
    assert item["properties"]["unsure"]["type"] == "boolean"
    prompt = inbox_ai._system_prompt()
    assert "advance_rows" in prompt and "ส่วนกลาง" in prompt and "ความผิด" in prompt and "อุปกรณ์" in prompt
    # a page answer with the new field passes the reader's own check, and the lines are unchanged (booking reads them as before)
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    if jsonschema is not None:
        jsonschema.validate({**{k: v for k, v in ADVANCE_NEW.items() if k != "n"}}, s)


def test_the_preview_says_how_many_rows_the_lists_gave(monkeypatch):
    monkeypatch.setattr(supabase, "configured", lambda: False)
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    pv = inbox_ai.from_pages([{**p, "n": i} for i, p in enumerate(json.loads(json.dumps(MID_PAGES + [ADVANCE_NEW])), 1)], "x.pdf", {})
    assert "ใบเบิกกลางเดือน 2 คน (ส่วนกลาง / ความผิด / อุปกรณ์) ใช้ในหน้าคำนวณเงินเดือน" in pv["note"]
    old = inbox_ai.from_pages(reads(), "x.pdf", {})
    assert "ส่วนกลาง / ความผิด" not in old["note"]
    assert [r["amount"] for r in pv["batches"][0]["rows"] if r.get("page") == 5] == [r["amount"] for r in old["batches"][0]["rows"] if r.get("page") == 5]


# ------------------------------------------------------------------ inbox_ai.reread_pages
def test_reread_replaces_only_the_given_pages_and_keeps_the_fixes(engine, db, monkeypatch, ai_on):
    row = scan_row(db)
    c = client_for(monkeypatch)
    before = row["preview"]
    out = inbox_ai.reread_pages({**before, "ai_usage": row["ai_usage"]}, [5], {})
    assert c.calls == 1 and engine["images"] == [f"accdocs/inbox/{FID}/p5.jpg"]
    new = {p["n"]: p for p in out["ai_pages"]}
    assert new[5]["advance_rows"] == ADVANCE_NEW["advance_rows"] and new[5]["n"] == 5
    assert all(new[n] == p for n, p in ((p["n"], p) for p in before["ai_pages"]) if n != 5)          # the other pages exactly as read
    assert out["line_edits"] == EDITS and out["hr_items"] == HR and out["ai_images"] == before["ai_images"]
    assert out["ai_usage"] == {"model": "m", "calls": 6, "input_tokens": 5000 + 1050, "output_tokens": 1200}
    assert out["reread"] == {"pages": [5], "failed": []}
    rows = {r.get("key"): r for r in out["batches"][0]["rows"]}
    assert rows["p2:l1"]["amount"] == 450 and rows["p5:l1"]["amount"] == 3500          # the same number of lines: the fixes still fit


def test_a_reread_page_keeps_its_fixes_only_while_its_line_keys_stand(engine, db, monkeypatch, ai_on):
    """Owner's review, 28 Nov 2026: the fixes are keyed by the batch lines a page builds, so that is what is compared. An advance page is
    always its one line p5:l1 -- a third person on it keeps the fix. An expense page numbers only its lines with an amount: the same
    number of raw lines with an unreadable one in front moves every key -- its fixes go, and a flag says so."""
    row = scan_row(db)
    three = {**ADVANCE_NEW, "written_total": 9000, "lines": ADVANCE_OLD["lines"] + [line("2026-11-08", "นาย ค SB-FR003", 3000, "1500")]}
    client_for(monkeypatch, answer=three)
    out = inbox_ai.reread_pages(row["preview"], [5], {})
    assert out["line_edits"] == EDITS and not any("บรรทัดไม่ตรงกับเดิม" in f for f in out["flags"])
    old2 = next(p for p in row["preview"]["ai_pages"] if p["n"] == 2)
    shifted = {**{k: v for k, v in old2.items() if k != "n"}, "lines": [line("2026-10-26", "อ่านไม่ออก", None)] + old2["lines"][:2]}
    use_client(monkeypatch, FakeClient([{}, shifted, {}, {}, {}]))
    out = inbox_ai.reread_pages(row["preview"], [2], {})
    assert out["line_edits"] == {"edits": {"p5:l1": {"amount": 3500}}, "added": []}
    assert any("หน้า 2 อ่านใหม่แล้วบรรทัดไม่ตรงกับเดิม" in f and "1 บรรทัด" in f for f in out["flags"])


def test_reread_refuses_what_it_cannot_read_and_counts_a_failed_call(engine, db, monkeypatch, ai_on):
    row = scan_row(db)
    pv = row["preview"]
    client_for(monkeypatch)
    for pages, says in (([], "เลือกหน้า"), ([9], "ไม่มีหน้า 9"), (["x"], "เลขหน้า"), (list(range(1, 12)), "ไม่เกิน")):
        with pytest.raises(inbox_ai.InboxAIError, match=says):
            inbox_ai.reread_pages(pv, pages)
    with pytest.raises(inbox_ai.InboxAIError, match="ไม่มีรูปหน้า 5"):
        inbox_ai.reread_pages({**pv, "ai_images": {"1": "accdocs/inbox/x/p1.jpg"}}, [5])
    use_client(monkeypatch, FakeClient([{}] * 5, fail={5: ValueError("boom")}))
    with pytest.raises(inbox_ai.InboxAIError, match="อ่านใหม่ไม่สำเร็จ") as exc:
        inbox_ai.reread_pages({**pv, "ai_usage": row["ai_usage"]}, [5])
    assert exc.value.usage["calls"] == 5                                  # nothing came back: the two attempts raised before any usage
    # one page of two fails: the other is used, the failed one keeps its earlier read and a flag says so
    use_client(monkeypatch, FakeClient([{}] * 4 + [ADVANCE_NEW], fail={4: ValueError("boom")}))
    out = inbox_ai.reread_pages(pv, [4, 5])
    assert out["reread"] == {"pages": [5], "failed": [4]} and any(f.startswith("อ่านหน้า 4 ใหม่ไม่สำเร็จ") for f in out["flags"])
    assert next(p for p in out["ai_pages"] if p["n"] == 4) == next(p for p in pv["ai_pages"] if p["n"] == 4)


def test_reread_is_off_when_the_ai_is(engine, db, monkeypatch):
    row = scan_row(db)
    with pytest.raises(inbox_ai.InboxAIError):
        inbox_ai.reread_pages(row["preview"], [5])


# ------------------------------------------------------------------ the engine
def test_the_engine_saves_the_reread_and_books_a_booked_file_again(engine, db, monkeypatch, ai_on):
    scan_row(db, status="booked", batches=["docs:2026-11:สระบุรี:mid"], booked_total=10500)
    client_for(monkeypatch)
    out = inbox.reread_pages(FID, [5])
    saved = db.rows[FID]
    assert engine["book"] == [("book", FID, ["docs:2026-11:สระบุรี:mid"])] and out["booked_again"] and saved["status"] == "booked"
    assert next(p for p in saved["preview"]["ai_pages"] if p["n"] == 5)["advance_rows"] == ADVANCE_NEW["advance_rows"]
    assert saved["ai_usage"]["calls"] == 6 and saved["preview"]["ai_usage"]["calls"] == 6 and "reread" not in saved["preview"]
    assert saved["preview"]["line_edits"] == EDITS and saved["preview"]["hr_items"] == HR
    assert out["reread"] == {"pages": [5], "failed": []} and out["preview"]["ai_pages"][4]["advance_rows"]


def test_a_booked_files_reread_that_cannot_book_keeps_its_pages_and_tokens(engine, db, monkeypatch, ai_on, readers):
    """Owner's review, 28 Nov 2026: the paid re-read is never thrown away. Its booking refused by the reader: the booking stays, the new
    pages wait beside it with their tokens, and อ่านใหม่ books them once the cause is fixed. A re-read parked with a question: its
    tokens are saved, and the answer is applied to ITS pages, not the booked ones."""
    scan_row(db, status="booked", batches=["docs:2026-11:สระบุรี:mid"], booked_total=10500)
    client_for(monkeypatch)
    real = readers[-1].book

    def refuse(p, fid):
        raise ValueError("ยอดไม่ตรง")
    monkeypatch.setattr(readers[-1], "book", refuse)
    with pytest.raises(inbox.InboxError, match="ลงบัญชีไม่สำเร็จ"):
        inbox.reread_pages(FID, [5])
    saved = db.rows[FID]
    assert saved["status"] == "booked" and saved["ai_usage"]["calls"] == 6 and saved["preview"]["ai_usage"]["calls"] == 6
    pend = saved["preview"]["pending"]
    assert pend["status"] == "read" and next(p for p in pend["preview"]["ai_pages"] if p["n"] == 5)["advance_rows"] == ADVANCE_NEW["advance_rows"]
    assert "advance_rows" not in next(p for p in saved["preview"]["ai_pages"] if p["n"] == 5)          # the booked read is untouched
    with pytest.raises(inbox.InboxError, match="ลงบัญชีใหม่ไม่สำเร็จ"):
        inbox.reread_pages(FID, [5])                                                                     # no second paid call on top
    monkeypatch.setattr(readers[-1], "book", real)
    inbox.reread(FID)
    saved = db.rows[FID]
    assert saved["status"] == "booked" and "pending" not in saved["preview"] and saved["ai_usage"]["calls"] == 6
    assert next(p for p in saved["preview"]["ai_pages"] if p["n"] == 5)["advance_rows"] == ADVANCE_NEW["advance_rows"]
    # parked with a question: the tokens are saved with it, and choose() rebuilds from the parked pages
    scan_row(db, status="booked", batches=["docs:2026-11:สระบุรี:mid"], booked_total=10500)
    row = db.rows[FID]
    new = inbox_ai.reread_pages({**row["preview"], "ai_usage": row["ai_usage"]}, [5], {})
    new.pop("reread")
    new["conflicts"] = [{"what": "month", "message": "เดือนไหน", "options": []}]
    inbox._reprocess({**row, "ai_usage": new["ai_usage"]}, {}, {FID}, fresh=new)
    saved = db.rows[FID]
    assert saved["preview"]["pending"]["status"] == "needs_choice" and saved["ai_usage"]["calls"] == 6 and saved["preview"]["ai_usage"]["calls"] == 6
    inbox._reprocess(saved, {}, {FID})
    saved = db.rows[FID]
    assert saved["status"] == "booked" and "pending" not in saved["preview"]
    assert next(p for p in saved["preview"]["ai_pages"] if p["n"] == 5)["advance_rows"] == ADVANCE_NEW["advance_rows"]


def test_the_engine_refuses_in_thai_and_keeps_a_failed_calls_tokens(engine, db, monkeypatch, ai_on):
    row = scan_row(db)
    before = json.loads(json.dumps(row["preview"]))
    use_client(monkeypatch, FakeClient([{}] * 5, fail={5: ValueError("boom")}))
    with pytest.raises(inbox.InboxError, match="อ่านใหม่ไม่สำเร็จ"):
        inbox.reread_pages(FID, [5])
    assert db.rows[FID]["preview"]["ai_pages"] == before["ai_pages"] and db.rows[FID]["status"] == "read"
    db.rows[FID]["preview"]["pending"] = {"kind": "scan", "status": "needs_choice", "preview": before, "choices": {}}
    with pytest.raises(inbox.InboxError, match="เลือกก่อน"):
        inbox.reread_pages(FID, [5])
    db.rows[FID]["preview"].pop("pending")
    db.rows[FID]["status"] = "ai_running"
    monkeypatch.setattr(inbox, "_running_ids", lambda: {FID})
    with pytest.raises(inbox.InboxError, match="AI กำลังอ่าน"):
        inbox.reread_pages(FID, [5])
    db.rows[FID]["status"] = "read"
    held, done = threading.Event(), threading.Event()

    def other_action():                                                # another action on the file (another request): refused, never queued
        with inbox._busy(FID):
            held.set()
            done.wait(5)
    t = threading.Thread(target=other_action)
    t.start()
    held.wait(5)
    try:
        with pytest.raises(inbox.Busy):
            inbox.reread_pages(FID, [5])
    finally:
        done.set()
        t.join()
    db.rows[FID]["kind"] = "payroll"
    with pytest.raises(inbox.Forbidden):
        inbox.reread_pages(FID, [5], see_pay=False)


# ------------------------------------------------------------------ the route and who may use it
def test_the_route_and_who_may_reread(engine, db, monkeypatch, ai_on):
    scan_row(db)
    client_for(monkeypatch)
    people = {"u_books": {"id": 1, "name": "คนทำบัญชี", "role": "staff", "menus": ["accounts_in"]},
              "u_pnl": {"id": 2, "name": "ดูงบ", "role": "staff", "menus": ["accounts"]}}
    monkeypatch.setattr(users, "session_user", lambda t: people.get(t))
    c = _client(monkeypatch)
    url = f"/api/accounts/inbox/{FID}/pages/reread"
    assert c.post(url, json={"pages": [5]}).status_code in (401, 403)                              # no token
    assert c.post(url, headers={"X-App-Token": "wrong"}, json={"pages": [5]}).status_code in (401, 403)
    assert c.post(url, headers={"X-App-Token": "u_pnl"}, json={"pages": [5]}).status_code == 403   # the P&L menu never feeds the books
    assert c.post(url, headers={"X-App-Token": "u_books"}, json={"pages": "5"}).status_code == 422
    r = c.post(url, headers={"X-App-Token": "u_books"}, json={"pages": [5]})
    assert r.status_code == 200, r.text
    assert r.json()["reread"] == {"pages": [5], "failed": []} and r.json()["preview"]["ai_pages"][4]["advance_rows"][1]["fault"] == "500"
    assert c.post(url, headers={"X-App-Token": "secret"}, json={"pages": [9]}).status_code == 422
    assert c.post("/api/accounts/inbox/55555555-5555-5555-5555-555555555555/pages/reread", headers={"X-App-Token": "secret"},
                  json={"pages": [5]}).status_code == 404
    assert users.allowed(["accounts_in"], url) and not users.allowed(["accounts"], url)


# ------------------------------------------------------------------ the check page's own helper (static/inboxcheck.js, in Node)
def test_the_check_page_reads_the_rows_of_an_advance_page(tmp_path):
    from tests.test_inbox_check_page import NODE, run
    if NODE is None:
        pytest.skip("Node is not installed")
    f = {"preview": {"ai_pages": [{"n": 5, **ADVANCE_NEW}, {"n": 6, **ADVANCE_OLD}]}}
    got = run("(() => ({ a: ickAdvRows(DATA.f, 5), b: ickAdvRows(DATA.f, 6) }))()", {"f": f}, tmp_path)
    assert got["a"]["old"] is False and len(got["a"]["rows"]) == 2 and got["a"]["sums"] == {"common": 980, "fault": 500, "other": 0}
    assert got["b"]["old"] is True and got["b"]["rows"] == []


def test_the_signed_copies_of_an_end_of_month_file_offer_no_paid_reread(tmp_path):
    """Owner's review, 28 Nov 2026: the pay page never reads the end-of-month file's advance pages (payroll.advance_lists), so no
    "อ่านหน้านี้ใหม่" (a paid call) and no "the pay page cannot use these" there -- the mid-month file's page still offers both."""
    from tests.test_inbox_check_page import NODE, run
    if NODE is None:
        pytest.skip("Node is not installed")
    test = """(() => {
      globalThis.el = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.filter((k) => k) });
      globalThis.fmtMoney = (n) => String(n);
      const words = (n) => [n.attrs.text || ""].concat(...(n.kids || []).map(words));
      const out = {};
      for (const half of ["end", "mid"]) {
        ick.f = { id: "f", preview: { half, ai_pages: [{ n: 6, page_kind: "advance" }] } };
        ick.editable = true;
        out[half] = words(ickAdvEl({ n: 6, image: "x.jpg" })).join(" | ");
      }
      return out;
    })()"""
    got = run(test, {}, tmp_path)
    assert "อ่านหน้านี้ใหม่" not in got["end"] and "ยังเอายอดเหล่านี้ไปใช้ไม่ได้" not in got["end"] and "ฉบับเซ็นรับ" in got["end"]
    assert "อ่านหน้านี้ใหม่ (เอาส่วนกลาง / ความผิด)" in got["mid"] and "ยังเอายอดเหล่านี้ไปใช้ไม่ได้" in got["mid"]
