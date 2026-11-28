"""ตรวจเอกสารก่อนลงบัญชี (owner, 28 Nov 2026): the owner's line fixes on an AI-read mid / end-of-month paper before ลงบัญชี
(app/inbox_ai.apply_line_edits / clean_line_edits, app/inbox.save_edits / page_image, the /edits and /page/<n> routes) and where every
line goes (ไปที่: dest / dest_kind). Fakes only: the page reads are made up, supabase is a dict in memory -- never the live database."""
import json

import pytest

from app import inbox, inbox_ai, inbox_r_scan, supabase, users
from app import inbox_common as ic
from tests.test_inbox import _client, db, fake_reader, readers  # noqa: F401 -- db / readers: the engine's in-memory inbox_files
from tests.test_inbox_ai import MID_PAGES, VEH, FakeDB, line, page, use_db

FID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def off_db(monkeypatch):
    monkeypatch.setattr(supabase, "configured", lambda: False)
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)


def reads(pages=MID_PAGES):
    return [{**p, "n": i} for i, p in enumerate(json.loads(json.dumps(pages)), 1)]


def lines_of(pv, n):
    return next(p["lines"] for p in pv["acc_docs_batch"]["pages"] if p["page"] == n)


def row_of(pv, key):
    return next(r for r in pv["batches"][0]["rows"] if r.get("key") == key)


def check(pv, n):
    return next(c for c in pv["page_checks"] if c["page"] == n)


# ------------------------------------------------------------------ keys and where each line goes
def test_every_line_has_a_stable_key_and_says_where_it_goes(off_db):
    pv = inbox_ai.from_pages(reads(), "x.pdf", {})
    assert [ln[5]["key"] for ln in lines_of(pv, 2)] == ["p2:l1", "p2:l2", "p2:l3"]
    b = pv["batches"][0]
    for e in b["entries"]:                                             # the key is the batch line number acc_docs.build books
        n, i = e["source_id"].split(":p")[1].split(":l")
        assert row_of(pv, f"p{n}:l{i}")["amount"] == e["lines"][0]["debit"]
    fuel = row_of(pv, "p3:l1")
    assert fuel["dest_kind"] == "book" and fuel["dest"] == "บัญชี 6130 น้ำมันเงินสด · รถ #5 (1ฒถ 2085)" and fuel["vehicle_id"] == "v5"
    assert row_of(pv, "p2:l2")["dest"] == "บัญชี 5410 ค่าล่วงเวลา (โอที)"
    memo = row_of(pv, "p4:page")
    assert memo["dest_kind"] == "noted" and memo["dest"] == "ไม่ลงบัญชี · ข้อมูลประกอบ · ตีห้า (SB-FR068)"
    assert {r["dest_kind"] for r in b["rows"]} == {"book", "noted"}


def test_advance_and_loan_lines_name_their_account(off_db):
    pv = inbox_ai.from_pages(reads([page("advance", title="เบิกกลางเดือน", written_total=6000,
                                         lines=[line("2026-11-08", "นาย ก", 3000, "1500"), line("2026-11-08", "นาย ข", 3000, "1500")]),
                                    page("expense", written_total=10000, lines=[line("2026-11-03", "ช่างแหยม กู้เงินโรงงาน", 10000, "1510")])]),
                             "กลางเดือนพฤศจิกา69 สระบุรี.pdf", {})
    assert row_of(pv, "p1:l1")["dest"] == "บัญชี 1500 เงินเบิกล่วงหน้า"
    assert row_of(pv, "p2:l1")["dest"] == "บัญชี 1510 เงินกู้พนักงาน"


def test_cash_fuel_lines_keep_their_text_and_say_why_in_dest(monkeypatch, off_db):
    monkeypatch.setattr(inbox_ai, "_cash_fuel_lines", lambda m, b, h: [{"day": "2026-10-27", "amount": 1500, "vehicle_id": "v5"},
                                                                        {"day": "2026-11-02", "amount": 1400, "vehicle_id": "v31"}])
    pv = inbox_ai.from_pages(reads(), "x.pdf", {})
    a, b = row_of(pv, "p3:l1"), row_of(pv, "p3:l2")
    assert (a["dest_kind"], a["dest"], a["text"], a["amount"], a["read_amount"]) == ("cashfuel", "มีแล้วในน้ำมันเงินสด", "เติมน้ำมัน (05)", 0.0, 1500.0)
    assert b["dest"] == "มีแล้วในน้ำมันเงินสด (1,400.00)" and b["text"] == "เติมน้ำมัน (31)"     # her amount differs: shown
    assert not any("ไม่ลงซ้ำ" in r["text"] for r in pv["batches"][0]["rows"])
    assert a["date"] == "2026-10-27" and a["vehicle_id"] == "v5" and check(pv, 3)["status"] == "ok"      # still counted in the page


# ------------------------------------------------------------------ the fixes
EDITS = {"edits": {"p2:l1": {"date": "2026-10-28", "text": "ถุงมือยาง 10 คู่", "amount": 450, "account": "5510"},
                   "p2:l3": {"amount": 930},                                  # the unsure line, read 880: the owner reads 930
                   "p3:l2": {"vehicle_id": "v5"},
                   "p2:l2": {"delete": True}},
         "added": [{"id": "x1", "page": 2, "date": "2026-11-04", "text": "ค่ารถรับจ้าง", "amount": 240, "account": "7900"}]}


def test_fixes_change_the_batch_the_entries_and_the_rows(off_db):
    pv = inbox_ai.from_pages(reads(), "x.pdf", {}, line_edits=EDITS)
    ls = lines_of(pv, 2)
    assert [ln[5]["key"] for ln in ls] == ["p2:l1", "p2:l3", "p2:x1"]
    assert ls[0][:5] == ("28/10", "ถุงมือยาง 10 คู่", 450.0, "5510", "ของใช้โรงงาน") and ls[0][5]["edited"]
    assert ls[1][1] == "ข้าวพนักงาน" and ls[1][2] == 930.0                   # looked at: the (อ่านไม่ชัด) mark goes
    assert ls[2][:5] == ("4/11", "ค่ารถรับจ้าง", 240.0, "7900", "อื่นๆ") and ls[2][5]["added"]
    b = pv["batches"][0]
    assert len(b["entries"]) == 5 and not any(e["description"].startswith("โอที") for e in b["entries"])
    assert next(e for e in b["entries"] if e["description"] == "ค่ารถรับจ้าง")["source_id"] == "docs:2026-11:สระบุรี:mid:p2:l3"
    assert [e["lines"][0]["vehicle_id"] for e in b["entries"] if e["lines"][0]["account"] == "6130"] == ["v5", "v5"]
    assert b["rows_total"] == 450 + 930 + 240 + 3000
    gone = row_of(pv, "p2:l2")
    assert (gone["dest_kind"], gone["dest"], gone["amount"], gone["read_amount"]) == ("deleted", "ลบแล้ว · ไม่ลง", 0.0, 120.0)
    added = row_of(pv, "p2:x1")
    assert added["dest_kind"] == "book" and added["added"] and added["edited"] and added["date"] == "2026-11-04"
    assert row_of(pv, "p2:l1")["edited"] and not row_of(pv, "p3:l1")["edited"]
    assert row_of(pv, "p3:l2")["dest"].endswith("รถ #5 (1ฒถ 2085)")
    c = check(pv, 2)                                                  # 450 + 930 + 240 = 1,620 against 1,500 written
    assert (c["read_total"], c["status"]) == (1620.0, "mismatch")
    assert pv["line_edits"] == EDITS


def test_page_totals_follow_the_fixes(off_db):
    pv = inbox_ai.from_pages(reads(), "x.pdf", {}, line_edits={"edits": {"p2:l3": {"delete": True}}})
    assert (check(pv, 2)["read_total"], check(pv, 2)["status"]) == (620.0, "mismatch")
    pv = inbox_ai.from_pages(reads(), "x.pdf", {}, line_edits={"edits": {"p2:l3": {"delete": True}},
                                                               "added": [{"id": "x1", "page": 2, "date": "2026-11-05", "text": "ข้าว",
                                                                          "amount": 880, "account": "7900"}]})
    assert (check(pv, 2)["read_total"], check(pv, 2)["status"]) == (1500.0, "ok")


def test_deleting_every_line_files_the_page_without_money(off_db):
    pv = inbox_ai.from_pages(reads(), "x.pdf", {}, line_edits={"edits": {k: {"delete": True} for k in ("p2:l1", "p2:l2", "p2:l3")}})
    assert 2 not in [p["page"] for p in pv["acc_docs_batch"]["pages"]] and check(pv, 2)["kind"] == "other"
    assert [r["dest_kind"] for r in pv["batches"][0]["rows"] if r["page"] == 2 and r["key"] != "p2:page"] == ["deleted"] * 3


def test_fixes_survive_a_choice_and_a_fresh_read_starts_without_them(off_db):
    pages = json.loads(json.dumps(MID_PAGES))
    pages[0]["date_to"], pages[0]["date_from"] = "2026-12-10", "2026-11-26"        # cover says Dec, the lines say Nov: a conflict
    pv = inbox_ai.from_pages(reads(pages), "x.pdf", {}, line_edits=EDITS)
    assert any(c["key"] == "month" for c in pv["conflicts"])
    again = inbox_ai.apply_choices(json.loads(json.dumps(pv)), {"month": "2026-11"})       # as stored in jsonb
    assert again["line_edits"] == EDITS and not again["conflicts"]
    assert [ln[5]["key"] for ln in lines_of(again, 2)] == ["p2:l1", "p2:l3", "p2:x1"]
    fresh = inbox_ai.from_pages(again["ai_pages"], "x.pdf", {"month": "2026-11"})
    assert "line_edits" not in fresh and [ln[5]["key"] for ln in lines_of(fresh, 2)] == ["p2:l1", "p2:l2", "p2:l3"]


def test_a_fixed_date_outside_the_half_after_another_month_is_kept_out_with_a_flag(off_db):
    pv = inbox_ai.from_pages(reads(), "x.pdf", {"month": "2026-12"}, line_edits={"edits": {"p2:l1": {"date": "2026-10-28", "amount": 450}}})
    ln = lines_of(pv, 2)[0]
    assert ln[0] == "26/10" and ln[2] == 450.0                                     # the date is not moved, the amount is still fixed
    assert any("อยู่นอกงวด" in f for f in pv["flags"])


def test_a_line_added_to_a_page_with_no_money_makes_it_a_money_page(off_db):
    pages = json.loads(json.dumps(MID_PAGES))
    pages[1]["lines"] = [line("2026-10-26", "ถุงมือ", None)]                       # no amount read on the whole page: a memo
    pv = inbox_ai.from_pages(reads(pages), "x.pdf", {})
    assert check(pv, 2)["kind"] == "other"
    pv = inbox_ai.from_pages(reads(pages), "x.pdf", {}, line_edits={"added": [{"id": "x7", "page": 2, "date": "2026-10-26", "text": "ถุงมือ",
                                                                               "amount": 1500, "account": "5510"}]})
    assert check(pv, 2)["kind"] == "expense" and check(pv, 2)["status"] == "ok" and row_of(pv, "p2:x7")["dest_kind"] == "book"


def test_the_same_bytes_dropped_again_never_take_another_rows_fixes(monkeypatch):
    import hashlib
    from tests.test_inbox_ai import pdf
    data = pdf(4)
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    first = inbox_ai.from_pages(reads(), "x.pdf", {}, line_edits=EDITS)
    use_db(monkeypatch, FakeDB(vehicles=VEH, inbox_files=[{"sha256": hashlib.sha256(data).hexdigest(), "preview": first}]))
    pv = inbox_r_scan.read(ic.sniff_file("x.pdf", data, {}))
    assert "line_edits" not in pv and len(lines_of(pv, 2)) == 3


# ------------------------------------------------------------------ checking what the page sends
def base_pv():
    return inbox_ai.from_pages(reads(), "x.pdf", {})


@pytest.mark.parametrize("edits, says", [
    ({"edits": {"p9:l1": {"amount": 5}}}, "ไม่พบบรรทัด"),
    ({"edits": {"p2:l1": {"date": "2026-11-11"}}}, "งวดของไฟล์นี้ (26/10/2026 – 10/11/2026)"),
    ({"edits": {"p2:l1": {"date": "2026-02-30"}}}, "วันที่ไม่ถูกต้อง"),
    ({"edits": {"p2:l1": {"amount": 0}}}, "มากกว่า 0"),
    ({"edits": {"p2:l1": {"amount": 10_000_000}}}, "10 ล้าน"),
    ({"edits": {"p2:l1": {"amount": "abc"}}}, "จำนวนเงินไม่ถูกต้อง"),
    ({"edits": {"p2:l1": {"text": "  "}}}, "1–200"),
    ({"edits": {"p2:l1": {"text": "ก" * 201}}}, "1–200"),
    ({"edits": {"p2:l1": {"account": "9999"}}}, "ไม่มีบัญชี 9999"),
    ({"edits": {"p2:l1": {"vehicle_id": "nope"}}}, "ไม่พบรถ"),
    ({"edits": {"p2:l1": {"party": "ร้าน"}}}, "แก้ช่อง party ไม่ได้"),
    ({"added": [{"id": "x1", "page": 4, "date": "2026-11-01", "text": "a", "amount": 5, "account": "7900"}]}, "เฉพาะหน้ารายการเบิก"),
    ({"added": [{"id": "x1", "page": 2, "date": "2026-11-01", "text": "a", "amount": 5}]}, "ให้ครบ"),
    ({"added": [{"id": "../x", "page": 2, "date": "2026-11-01", "text": "a", "amount": 5, "account": "7900"}]}, "รหัสบรรทัด"),
    ({"edits": {f"p2:l{i}": {"amount": 1} for i in range(1, 302)}}, "ไม่เกิน 300"),
    ("nope", "ไม่ถูกต้อง"),
])
def test_what_the_page_sends_is_checked_strictly(off_db, edits, says):
    with pytest.raises(ValueError, match=says.replace("(", r"\(").replace(")", r"\)")):
        inbox_ai.clean_line_edits(base_pv(), edits, VEH)


def test_clean_fixes_fold_an_added_lines_fix_and_keep_a_deleted_line_undoable(off_db):
    pv = inbox_ai.from_pages(reads(), "x.pdf", {}, line_edits={"edits": {"p2:l2": {"delete": True}}})
    got = inbox_ai.clean_line_edits(pv, {"edits": {"p2:l2": {"delete": False, "amount": "1,200"}, "p2:x1": {"amount": 99},
                                                   "p2:l1": {"vehicle_id": ""}},
                                         "added": [{"id": "x1", "page": 2, "date": "2026-11-01", "text": " ค่าส่ง ", "amount": 5, "account": "7300"}]},
                                     VEH)
    assert got == {"edits": {"p2:l2": {"amount": 1200.0}, "p2:l1": {"vehicle_id": None}},
                   "added": [{"id": "x1", "page": 2, "date": "2026-11-01", "text": "ค่าส่ง", "amount": 99.0, "account": "7300"}]}
    with pytest.raises(ValueError, match="อ่านใหม่"):                       # read before keys existed: อ่านใหม่ first (free)
        old = json.loads(json.dumps(pv))
        for p in old["acc_docs_batch"]["pages"]:
            for ln in p["lines"]:
                ln[5].pop("key", None)
        old.pop("line_edits")
        inbox_ai.clean_line_edits(old, {"edits": {"p2:l1": {"amount": 1}}}, VEH)


# ------------------------------------------------------------------ the engine and the routes
def scan_row(db, status="read", **extra):
    pv = inbox_ai.from_pages(reads(), "x.pdf", {})
    db.rows[FID] = {"id": FID, "file_name": "x.pdf", "kind": "scan", "status": status, "storage_path": f"inbox/{FID}.pdf",
                    "mime_type": "application/pdf", "batches": [], "choices": {}, "branches": ["สระบุรี"], "_t": 0, "sha256": "s",
                    "ai_usage": {"calls": 4}, "month": "2026-11", "preview": json.loads(json.dumps({**pv, "ai_images": {
                        str(i): f"accdocs/inbox/{FID}/p{i}.jpg" for i in range(1, 5)}})), **extra}
    return db.rows[FID]


@pytest.fixture
def engine(monkeypatch, db, readers):
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    monkeypatch.setattr(inbox_ai, "_cash_fuel_lines", lambda m, b, h: [])
    monkeypatch.setattr(inbox_ai, "_earlier_docs", lambda *a: [])
    log: list = []
    readers.append(fake_reader("scan", 0, log=log))
    return log



def test_saving_fixes_rebuilds_the_preview_without_booking(db, engine):
    scan_row(db)
    out = inbox.save_edits(FID, EDITS)
    row = db.rows[FID]
    assert engine == [] and row["status"] == "read" and out["status"] == "read" and not out["booked_again"]
    assert row["preview"]["line_edits"] == EDITS and out["preview"]["line_edits"] == EDITS
    assert out["preview"]["batches"][0]["rows_total"] == 450 + 930 + 240 + 3000
    assert ["5330", "ถุงและบรรจุภัณฑ์"] in out["accounts"] and out["vehicles"][0]["id"] == "v5" and "6130" in out["truck_accounts"]
    inbox.save_edits(FID, {"edits": {}, "added": []})                  # everything taken back: as read
    assert "line_edits" not in db.rows[FID]["preview"] and len(lines_of(db.rows[FID]["preview"], 2)) == 3


def test_fixes_on_a_booked_file_book_it_again_with_them(db, engine):
    scan_row(db, status="booked", batches=["docs:2026-11:สระบุรี:mid"], booked_total=4500)
    out = inbox.save_edits(FID, {"edits": {"p2:l2": {"delete": True}}})
    assert engine == [("book", FID, ["docs:2026-11:สระบุรี:mid"])] and out["booked_again"] and out["status"] == "booked"
    assert db.rows[FID]["booked_total"] == 4380 and db.rows[FID]["preview"]["line_edits"] == {"edits": {"p2:l2": {"delete": True}}, "added": []}


def test_fixes_are_refused_in_thai_and_leave_the_file_as_it_was(db, engine):
    before = json.loads(json.dumps(scan_row(db)["preview"]))
    with pytest.raises(inbox.InboxError, match="งวดของไฟล์นี้"):
        inbox.save_edits(FID, {"edits": {"p2:l1": {"date": "2026-12-01"}}})
    assert db.rows[FID]["preview"] == before
    db.rows[FID]["preview"]["pending"] = {"kind": "scan", "status": "needs_choice", "preview": before, "choices": {}}
    with pytest.raises(inbox.InboxError, match="เลือกก่อน"):
        inbox.save_edits(FID, EDITS)
    db.rows[FID] = {**db.rows[FID], "preview": ic.empty_preview("cheques", "เช็ค", batches=[])}
    with pytest.raises(inbox.InboxError, match="แก้รายการที่นี่ไม่ได้"):
        inbox.save_edits(FID, EDITS)


def test_page_pictures_and_fixes_over_the_routes(db, engine, monkeypatch):
    scan_row(db)
    got = []
    monkeypatch.setattr(supabase, "storage_download_private", lambda p: got.append(p) or b"\xff\xd8jpeg")
    c = _client(monkeypatch)
    h = {"X-App-Token": "secret"}
    r = c.get(f"/api/accounts/inbox/{FID}/page/2?token=secret")
    assert r.status_code == 200 and r.content == b"\xff\xd8jpeg" and r.headers["content-type"] == "image/jpeg"
    assert got == [f"accdocs/inbox/{FID}/p2.jpg"]
    assert c.get(f"/api/accounts/inbox/{FID}/page/9", headers=h).status_code == 404
    db.rows[FID]["preview"]["ai_images"]["3"] = "accdocs/../payroll/secret.jpg"              # never outside the drop box's own pictures
    assert c.get(f"/api/accounts/inbox/{FID}/page/3", headers=h).status_code == 404
    ver = c.get(f"/api/accounts/inbox/{FID}", headers=h).json()["edits_version"]
    r = c.post(f"/api/accounts/inbox/{FID}/edits", headers=h, json={"line_edits": {"edits": {"p2:l1": {"amount": 450}}}, "version": ver})
    assert r.status_code == 200, r.text
    assert row_of(r.json()["preview"], "p2:l1")["amount"] == 450.0 and r.json()["edits_version"] != ver
    bad = c.post(f"/api/accounts/inbox/{FID}/edits", headers=h,
                 json={"line_edits": {"edits": {"p2:l1": {"amount": -1}}}, "version": r.json()["edits_version"]})
    assert bad.status_code == 422 and "มากกว่า 0" in bad.json()["detail"]
    stale = c.post(f"/api/accounts/inbox/{FID}/edits", headers=h, json={"line_edits": {"edits": {"p3:l2": {"amount": 1400}}}, "version": ver})
    assert stale.status_code == 422 and "จากที่อื่น" in stale.json()["detail"]
    no_ver = c.post(f"/api/accounts/inbox/{FID}/edits", headers=h, json={"line_edits": {"edits": {"p3:l2": {"amount": 1400}}}})
    assert no_ver.status_code == 422
    assert row_of(db.rows[FID]["preview"], "p2:l1")["amount"] == 450.0 and "p3:l2" not in db.rows[FID]["preview"]["line_edits"]["edits"]


def test_a_tab_opened_before_another_save_cannot_wipe_its_fixes(db, engine):
    scan_row(db, status="booked", batches=["docs:2026-11:สระบุรี:mid"], booked_total=4500)
    opened = inbox.get(FID)["edits_version"]                           # A and B open the same file
    inbox.save_edits(FID, {"edits": {"p2:l1": {"delete": True}}}, version=opened)              # A deletes a line
    with pytest.raises(inbox.InboxError, match="จากที่อื่น"):                                   # B, opened before, fixes another
        inbox.save_edits(FID, {"edits": {"p3:l2": {"amount": 1400}}}, version=opened)
    assert db.rows[FID]["preview"]["line_edits"]["edits"] == {"p2:l1": {"delete": True}}
    assert len(engine) == 1                                            # booked again once, with A's delete


def test_a_payroll_files_pages_need_the_pay_menu(db, engine, monkeypatch):
    scan_row(db, kind="payroll")
    monkeypatch.setattr(supabase, "storage_download_private", lambda p: b"jpeg")
    with pytest.raises(inbox.Forbidden):
        inbox.page_image(FID, 1, see_pay=False)
    assert inbox.page_image(FID, 1, see_pay=True) == b"jpeg"


def test_the_new_paths_belong_to_the_inputs_menu_only():
    for path in (f"/api/accounts/inbox/{FID}/page/1", f"/api/accounts/inbox/{FID}/edits"):
        assert users.allowed(["accounts_in"], path) and not users.allowed(["accounts"], path)


def test_the_check_page_route_is_a_sub_page_of_the_inputs_menu():
    hashes = users.MENUS["accounts_in"]["hash"]
    assert f"#accounts-in/check={FID}".startswith(f"{hashes}/")       # users.js staffMayOpen: a menu's own sub-pages


# ------------------------------------------------------------------ the truck a fix picks, and cash fuel after a delete (review, 28 Nov 2026)
def test_a_truck_picked_without_a_plate_is_booked_to_that_truck(monkeypatch):
    veh = VEH + [{"id": "kx", "vehicle_number": "5", "plate_number": None, "branch": "แก่งคอย"}]
    monkeypatch.setattr(supabase, "configured", lambda: False)
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: veh)
    le = {"edits": {"p2:l2": {"vehicle_id": "kx", "account": "6100"}},
          "added": [{"id": "x1", "page": 2, "date": "2026-11-01", "text": "ซ่อม", "amount": 50, "account": "6100", "vehicle_id": "kx"}]}
    pv = inbox_ai.from_pages(reads(), "x.pdf", {}, line_edits=le)       # a สระบุรี file; truck 5 of สระบุรี is v5
    for key in ("p2:l2", "p2:x1"):
        r = row_of(pv, key)
        assert r["vehicle_id"] == "kx" and r["dest"].endswith("รถ 5") and r["branch"] == "แก่งคอย"
    assert [e["lines"][0]["vehicle_id"] for e in pv["batches"][0]["entries"] if e["lines"][0]["account"] == "6100"] == ["kx", "kx"]


def test_deleting_her_paper_line_never_skips_another_trucks_fuel(monkeypatch, off_db):
    monkeypatch.setattr(inbox_ai, "_cash_fuel_lines", lambda m, b, h: [{"day": "2026-10-27", "amount": 1500, "vehicle_id": "v5"}])
    pv = inbox_ai.from_pages(reads(), "x.pdf", {}, line_edits={"edits": {"p3:l1": {"delete": True}}})
    assert row_of(pv, "p3:l2")["dest_kind"] == "book" and row_of(pv, "p3:l1")["dest_kind"] == "deleted"   # truck 31 is not hers
    pv = inbox_ai.from_pages(reads(), "x.pdf", {}, line_edits={"edits": {"p3:l1": {"date": "2026-10-29"}}})
    assert row_of(pv, "p3:l1")["dest_kind"] == "cashfuel"              # her truck, another day (a date misread): still hers


def test_files_read_before_the_check_page_keep_the_dialog(off_db):
    pv = inbox_ai.from_pages(reads(), "x.pdf", {})
    assert inbox.checkable(pv)
    old = json.loads(json.dumps(pv))
    for r in old["batches"][0]["rows"]:
        r.pop("key", None)
        r.pop("dest_kind", None)
    assert not inbox.checkable(old) and not inbox.checkable(ic.empty_preview("cheques", "เช็ค", batches=[]))
