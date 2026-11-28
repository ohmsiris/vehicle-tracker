"""ใบเบิกกลางเดือน's per-person table fixed on ตรวจเอกสารก่อนลงบัญชี (owner, 29 Nov 2026: "and how do I edit the numbers"): the owner's
fixes of รหัส / ยอดเบิก / ส่วนกลาง / ความผิด / อุปกรณ์ (preview["line_edits"]["rows"], app/inbox_ai.clean_row_edits), laid over the page
read (app/payroll.py edit_page) for the books (the page's 1500 line) and for the pay page (advances and deductions); kept by a choice and
อ่านใหม่, dropped for a page read again by the AI. Fakes only: the page reads are made up, supabase is a dict in memory -- never the live
database."""
import json

import pytest

from app import inbox, inbox_ai
from app import payroll as pw
from tests.test_inbox import _client, db, fake_reader, readers  # noqa: F401 -- db / readers: the engine's in-memory inbox_files
from tests.test_inbox_ai import MID_PAGES, VEH, FakeClient, ai_on, line, page, use_client  # noqa: F401 -- ai_on: a fixture
from tests.test_inbox_check_page import NODE, run

FID = "66666666-6666-6666-6666-666666666666"
TITLE = "รายการเบิกกลางเดือน : ช่าง / ออฟฟิศ / ฝ่ายผลิต"


def arow(code, amount, common="", fault="", other="", unsure=False, name=None):
    return {"code": code, "name": name if name is not None else f"นาย {code[-1]} ทดสอบ", "amount": amount, "common": common, "fault": fault,
            "other": other, "unsure": unsure}


# page 5: three people, written total 9,000; page 6: a second list page read before the table existed
ADV = page("advance", title=TITLE, written_total=9000,
           lines=[line("2026-11-08", "นาย 1 ทดสอบ SB-FR001", 3000, "1500"), line("2026-11-08", "นาย 2 ทดสอบ SB-FR002", 3000, "1500"),
                  line("2026-11-08", "นาย 3 ทดสอบ SB-FR003", 3000, "1500")],
           advance_rows=[arow("SB-FR001", "3,000", common="350"), arow("SB-FR002", "3000", common="630", fault="500", unsure=True),
                         arow("SB-FR003", "3000"), arow("", "9,000", name="รวม")])
OLD = page("advance", title="รายการเบิกกลางเดือน : พนักงานขับรถ", written_total=2000, lines=[line("2026-11-08", "นาย 7 SB-PD007", 2000, "1500")])


def reads():
    return [{**p, "n": i} for i, p in enumerate(json.loads(json.dumps(MID_PAGES + [ADV, OLD])), 1)]


@pytest.fixture
def off_db(monkeypatch):
    from app import supabase
    monkeypatch.setattr(supabase, "configured", lambda: False)
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)


def pv_of(edits=None):
    return inbox_ai.from_pages(reads(), "กลางเดือน.pdf", {}, line_edits=edits)


def check(pv, n):
    return next(c for c in pv["page_checks"] if c["page"] == n)


def adv_line(pv, n=5):
    return next(r for r in pv["batches"][0]["rows"] if r.get("key") == f"p{n}:l1")


FIX = {"5": {"edits": {"1": {"amount": 2500, "fault": "", "ok": True}, "2": {"delete": True}},
             "added": [{"id": "r1", "code": "SB-FR009", "name": "นาย 9 ทดสอบ", "amount": 3500, "common": 100, "fault": "", "other": ""}]}}


# ------------------------------------------------------------------ the check: strict, in Thai
@pytest.mark.parametrize("rows, says", [
    ({"4": {"edits": {"0": {"amount": 1}}}}, "เฉพาะหน้าใบเบิกกลางเดือน (หน้า 4)"),
    ({"9": {"edits": {"0": {"amount": 1}}}}, "เฉพาะหน้าใบเบิกกลางเดือน (หน้า 9)"),
    ({"6": {"edits": {"0": {"amount": 1}}}}, "หน้า 6 อ่านก่อนมีตารางรายคน"),
    ({"5": {"edits": {"4": {"amount": 1}}}}, "ไม่พบแถวที่ 4"),
    ({"5": {"edits": {"x": {"amount": 1}}}}, "ไม่พบแถวที่ x"),
    ({"5": {"edits": {"0": {"amount": -5}}}}, "ยอดเบิกต้องไม่ติดลบ"),
    ({"5": {"edits": {"0": {"common": 1_000_000}}}}, "ส่วนกลางต้องไม่ติดลบและน้อยกว่า 1,000,000"),
    ({"5": {"edits": {"0": {"fault": "ห้าร้อย"}}}}, "ความผิดต้องเป็นตัวเลข"),
    ({"5": {"edits": {"0": {"other": True}}}}, "อุปกรณ์ต้องเป็นตัวเลข"),
    ({"5": {"edits": {"0": {"code": "SB-FR01"}}}}, "รหัสพนักงานต้องเป็นแบบ SB-XX000"),
    ({"5": {"edits": {"0": {"name": "x"}}}}, "แก้ช่อง name ไม่ได้"),
    ({"5": {"added": [{"id": "r1", "amount": 500}]}}, "ใส่รหัสพนักงานหรือชื่อ"),
    ({"5": {"added": [{"id": "x1", "code": "SB-FR009"}]}}, "รหัสแถวไม่ถูกต้อง"),
    ({"5": {"added": [{"id": "r1", "code": "SB-FR009"}, {"id": "r1", "code": "SB-FR010"}]}}, "รหัสแถวไม่ถูกต้อง"),
    ({"5": {"oops": {}}}, "ข้อมูลที่แก้ในตารางรายคนไม่ถูกต้อง"),
    (["5"], "ข้อมูลที่แก้ในตารางรายคนไม่ถูกต้อง"),
])
def test_what_the_table_sends_is_checked_strictly(off_db, rows, says):
    with pytest.raises(ValueError, match=says.replace("(", r"\(").replace(")", r"\)")):
        inbox_ai.clean_line_edits(pv_of(), {"edits": {}, "added": [], "rows": rows}, VEH)


def test_clean_rows_are_numbers_or_blank_and_codes_upper_case(off_db):
    got = inbox_ai.clean_line_edits(pv_of(), {"rows": {"5": {"edits": {"0": {"amount": "3,500", "common": "-", "code": " sb-fr011 "},
                                                                      "1": {"ok": True, "delete": False}, "3": {}},
                                                            "added": [{"id": "r1", "name": "  นาย  ใหม่ ", "amount": "0"}]}}}, VEH)
    assert got == {"edits": {}, "added": [], "rows": {"5": {
        "edits": {"0": {"amount": 3500.0, "common": "", "code": "SB-FR011"}, "1": {"ok": True}},
        "added": [{"id": "r1", "code": "", "name": "นาย ใหม่", "amount": 0.0, "common": "", "fault": "", "other": ""}]}}}
    # nothing on the rows: no "rows" key at all (the line fixes' shape as before)
    assert inbox_ai.clean_line_edits(pv_of(), {"edits": {"p2:l1": {"amount": 450}}, "rows": {"5": {"edits": {"0": {}}}}}, VEH) == {
        "edits": {"p2:l1": {"amount": 450.0}}, "added": []}


# ------------------------------------------------------------------ laid over the page read: the rows and the booked lines agree
def test_edit_page_makes_the_fixed_rows_the_persons_lines():
    p = {**ADV, "n": 5}
    assert pw.pair_rows(p) == ([0, 1, 2, None], [])
    out = pw.edit_page(p, FIX["5"])
    assert p == {**ADV, "n": 5}                                                       # the read itself is untouched
    assert [r["code"] for r in out["advance_rows"]] == ["SB-FR001", "SB-FR002", "", "SB-FR009"]          # SB-FR003 deleted, SB-FR009 added
    assert out["advance_rows"][1]["amount"] == 2500 and out["advance_rows"][1]["unsure"] is False and out["advance_rows"][1]["fault"] == ""
    assert [(ln["text"], ln["amount"]) for ln in out["lines"]] == [
        ("นาย 1 ทดสอบ SB-FR001", 3000), ("SB-FR002 นาย 2 ทดสอบ", 2500), ("SB-FR009 นาย 9 ทดสอบ", 3500)]
    got = {ln["code"]: ln["amount"] for ln in pw.page_lines(out)}
    assert got == {"SB-FR001": 3000, "SB-FR002": 2500, "SB-FR009": 3500}
    assert {r["code"]: r["common"] for r in pw.page_rows(out)} == {"SB-FR001": 350, "SB-FR002": 630, "SB-FR009": 100}


def test_a_code_fix_moves_the_advance_and_ok_alone_only_clears_the_mark():
    p = {**ADV, "n": 5}
    out = pw.edit_page(p, {"edits": {"2": {"code": "SB-FR008"}}})
    assert {ln["code"]: ln["amount"] for ln in pw.page_lines(out)} == {"SB-FR001": 3000, "SB-FR002": 3000, "SB-FR008": 3000}
    ok = pw.edit_page(p, {"edits": {"1": {"ok": True}}})
    assert ok["advance_rows"][1]["unsure"] is False and ok["advance_rows"][1]["fault"] == "500"
    assert [ln["amount"] for ln in ok["lines"]] == [3000, 3000, 3000]
    # a row fixed to no amount takes its line away; a row with no amount read keeps the line's amount when another cell is fixed
    gone = pw.edit_page(p, {"edits": {"0": {"amount": ""}}})
    assert [ln["amount"] for ln in gone["lines"]] == [3000, 3000]
    blank = {**p, "advance_rows": [arow("SB-FR001", "")] + p["advance_rows"][1:]}
    kept = pw.edit_page(blank, {"edits": {"0": {"common": 200}}})
    assert kept["lines"][0]["amount"] == 3000 and kept["advance_rows"][0]["common"] == 200
    # the total row is nobody's: fixing it never books it
    assert len(pw.edit_page(p, {"edits": {"3": {"amount": 12000}}})["lines"]) == 3
    assert pw.edit_page(p, None) is p and pw.edit_page(p, {"edits": {}, "added": []}) is p


# ------------------------------------------------------------------ the books: the page's 1500 line and its written-total check
def test_the_booked_line_is_the_sum_of_the_fixed_table(off_db):
    plain = pv_of()
    assert adv_line(plain)["amount"] == 9000 and check(plain, 5)["status"] == "ok"
    assert plain["advance_pairs"] == {"5": {"line": [3000, 3000, 3000, None], "rest": 0, "rest_lines": []}}
    pv = pv_of({"edits": {}, "added": [], "rows": FIX})
    assert adv_line(pv)["amount"] == 3000 + 2500 + 3500 and check(pv, 5)["read_total"] == 9000 and check(pv, 5)["status"] == "ok"
    off = pv_of({"edits": {}, "added": [], "rows": {"5": {"edits": {"0": {"amount": 2000}}, "added": []}}})
    assert adv_line(off)["amount"] == 8000 and check(off, 5)["status"] == "mismatch"
    assert pv["ai_pages"] == plain["ai_pages"]                                        # the reads stay as the AI gave them
    assert "ใบเบิกกลางเดือน 4 คน" in pv["note"]                                         # the table as fixed (3 kept + 1 added, the total row too)


def test_a_saved_fix_of_a_page_no_longer_an_advance_list_is_left_out_with_a_flag(off_db):
    rs = reads()
    rs[4] = {**rs[4], "page_kind": "memo"}
    pv = inbox_ai.from_pages(rs, "x.pdf", {}, line_edits={"edits": {}, "added": [], "rows": FIX})
    assert any("การแก้ตารางรายคนที่บันทึกไว้ของหน้า 5 ใช้ไม่ได้" in f for f in pv["flags"])


def test_the_fixes_survive_a_choice(off_db):
    pv = pv_of({"edits": {"p2:l1": {"amount": 450}}, "added": [], "rows": FIX})
    again = inbox_ai.apply_choices(json.loads(json.dumps(pv)), {"month": "2026-11"})
    assert again["line_edits"]["rows"] == FIX and adv_line(again)["amount"] == 9000
    assert next(r for r in again["batches"][0]["rows"] if r.get("key") == "p2:l1")["amount"] == 450


# ------------------------------------------------------------------ the engine: save, re-book, the version, the route
def scan_row(db, status="read", edits=None, **extra):
    pv = inbox_ai.from_pages(reads(), "กลางเดือน.pdf", {}, line_edits=edits)
    pv = {**pv, "ai_images": {str(i): f"accdocs/inbox/{FID}/p{i}.jpg" for i in range(1, 7)}}
    db.rows[FID] = {"id": FID, "file_name": "กลางเดือน.pdf", "kind": "scan", "status": status, "storage_path": f"inbox/{FID}.pdf",
                    "mime_type": "application/pdf", "batches": [], "choices": {}, "branches": ["สระบุรี"], "_t": 0, "sha256": "s",
                    "ai_usage": {"model": "m", "calls": 6, "input_tokens": 6000, "output_tokens": 1200}, "month": "2026-11",
                    "preview": json.loads(json.dumps(pv)), **extra}
    return db.rows[FID]


@pytest.fixture
def engine(monkeypatch, db, readers):
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    monkeypatch.setattr(inbox_ai, "_cash_fuel_lines", lambda m, b, h: [])
    monkeypatch.setattr(inbox_ai, "_earlier_docs", lambda *a: [])
    log: list = []
    readers.append(fake_reader("scan", 0, log=log))
    return log


def test_saving_the_table_rebuilds_and_a_booked_file_is_booked_again(db, engine):
    scan_row(db, status="booked", batches=["docs:2026-11:สระบุรี:mid"], booked_total=4500 + 9000 + 2000)
    v0 = inbox.get(FID)["edits_version"]
    out = inbox.save_edits(FID, {"edits": {}, "added": [], "rows": {"5": {"edits": {"0": {"amount": "2,000"}}, "added": []}}}, version=v0)
    assert engine == [("book", FID, ["docs:2026-11:สระบุรี:mid"])] and out["booked_again"] and out["status"] == "booked"
    row = db.rows[FID]
    assert row["preview"]["line_edits"] == {"edits": {}, "added": [], "rows": {"5": {"edits": {"0": {"amount": 2000.0}}, "added": []}}}
    assert adv_line(row["preview"])["amount"] == 8000 and row["booked_total"] == 4500 + 8000 + 2000
    assert out["edits_version"] != v0                                                 # a row fix changes the version too
    with pytest.raises(inbox.InboxError, match="จากที่อื่น"):                            # a tab opened before cannot wipe it
        inbox.save_edits(FID, {"edits": {}, "added": []}, version=v0)
    with pytest.raises(inbox.InboxError, match="ยอดเบิกต้องไม่ติดลบ"):
        inbox.save_edits(FID, {"rows": {"5": {"edits": {"0": {"amount": -1}}}}}, version=out["edits_version"])
    inbox.save_edits(FID, {"edits": {}, "added": [], "rows": {}}, version=out["edits_version"])     # all taken back: as read
    assert "line_edits" not in db.rows[FID]["preview"] and adv_line(db.rows[FID]["preview"])["amount"] == 9000


def test_the_table_over_the_route(db, engine, monkeypatch):
    scan_row(db)
    c = _client(monkeypatch)
    h = {"X-App-Token": "secret"}
    ver = c.get(f"/api/accounts/inbox/{FID}", headers=h).json()["edits_version"]
    r = c.post(f"/api/accounts/inbox/{FID}/edits", headers=h, json={"line_edits": {"edits": {}, "added": [], "rows": FIX}, "version": ver})
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["line_edits"]["rows"]["5"]["added"][0]["code"] == "SB-FR009" and adv_line(r.json()["preview"])["amount"] == 9000
    bad = c.post(f"/api/accounts/inbox/{FID}/edits", headers=h,
                 json={"line_edits": {"rows": {"5": {"edits": {"0": {"code": "ก"}}}}}, "version": r.json()["edits_version"]})
    assert bad.status_code == 422 and "SB-XX000" in bad.json()["detail"]


def test_reread_without_ai_keeps_the_table_and_reread_pages_drops_only_that_pages(db, engine, monkeypatch, ai_on):
    edits = {"edits": {}, "added": [], "rows": FIX}
    scan_row(db, edits=edits)
    inbox.reread(FID)                                                                  # อ่านใหม่ (no AI): kept
    assert db.rows[FID]["preview"]["line_edits"]["rows"] == FIX and adv_line(db.rows[FID]["preview"])["amount"] == 9000
    other = {"6": {"edits": {}, "added": [{"id": "r2", "code": "SB-PD008", "amount": 100}]}}
    pv = {**db.rows[FID]["preview"], "ai_pages": [p if p["n"] != 6 else {**p, "advance_rows": []} for p in db.rows[FID]["preview"]["ai_pages"]]}
    pv["line_edits"] = {**edits, "rows": {**FIX, **other}}
    use_client(monkeypatch, FakeClient([{}] * 4 + [{**ADV, "work_rows": [], "advance_rows": [arow("SB-FR001", "3000")]}]))
    out = inbox_ai.reread_pages(pv, [5], {}, fetch=lambda path: b"\xff\xd8 jpeg")
    assert out["line_edits"]["rows"] == other                                         # page 5 read again: its table starts afresh
    assert any("การแก้ตารางรายคนของหน้านี้ที่บันทึกไว้ 3 แถวถูกเอาออก" in f for f in out["flags"])
    only = {**pv, "line_edits": edits}
    use_client(monkeypatch, FakeClient([{}] * 4 + [{**ADV, "work_rows": []}]))
    gone = inbox_ai.reread_pages(only, [5], {}, fetch=lambda path: b"\xff\xd8 jpeg")
    assert "line_edits" not in gone or not inbox_ai.has_edits(gone.get("line_edits"))


# ------------------------------------------------------------------ the pay page: advances from the fixed mid-month table, deductions too
def test_the_pay_page_takes_the_fixed_table():
    def file(fid, half, n, rows, fixes, written=6000, at="1"):
        pg = {"n": n, "page_kind": "advance", "title": TITLE, "written_total": written, "employee_codes": [],
              "lines": [{"text": "SB-FR001 นายหนึ่ง ทดสอบ", "amount": 3000, "account": "1500"}, {"text": "SB-FR002 นายสอง ทดสอบ", "amount": 3000, "account": "1500"}],
              "advance_rows": rows}
        return {"id": fid, "file_name": f"{fid}.pdf", "status": "booked", "uploaded_at": at, "half": half, "branches": ["สระบุรี"], "pages": [pg],
                "row_edits": fixes}
    rows = [arow("SB-FR001", "3000", name="นายหนึ่ง ทดสอบ"), arow("SB-FR002", "3000", name="นายสอง ทดสอบ")]
    emp = [{"id": f"E{i}", "code": f"SB-FR00{i}", "full_name": f"นาย{w} ทดสอบ", "status": "active", "branch": "สระบุรี"} for i, w in ((1, "หนึ่ง"), (2, "สอง"), (3, "สาม"))]
    mid_fix = {"9": {"edits": {"1": {"amount": 2000, "common": 400}}, "added": [{"id": "r1", "code": "SB-FR003", "name": "นายสาม ทดสอบ", "amount": 1000,
                                                                              "common": 50, "fault": "", "other": ""}]}}
    got = pw.advance_view([file("m", "mid", 9, rows, mid_fix)], emp)
    assert {e: [ln["amount"] for ln in lines] for e, lines in got["by_person"].items()} == {"E1": [3000], "E2": [2000], "E3": [1000]}
    assert got["branches"]["สระบุรี"]["complete"] is True                              # 3,000 + 2,000 + 1,000 = the 6,000 written
    assert {e: [r["common"] for r in rs] for e, rs in got["deduct"].items()} == {"E1": [0], "E2": [400], "E3": [50]}
    # the signed copy's fixes decide the deductions; its amounts never the advance
    end_fix = {"11": {"edits": {"0": {"common": 350, "amount": 9999}}, "added": []}}
    both = pw.advance_view([file("m", "mid", 9, rows, mid_fix), file("e", "end", 11, rows, end_fix, at="2")], emp)
    assert {e: [r["common"] for r in rs] for e, rs in both["deduct"].items()} == {"E1": [350], "E2": [0]}
    assert {e: [ln["amount"] for ln in lines] for e, lines in both["by_person"].items()} == {"E1": [3000], "E2": [2000], "E3": [1000]}
    # the fixes as the table stores them (preview.line_edits.rows) when the row has no row_edits column
    plain = file("m", "mid", 9, rows, None)
    plain.pop("row_edits")
    plain["preview"] = {"line_edits": {"edits": {}, "added": [], "rows": mid_fix}}
    assert {e: [ln["amount"] for ln in lines] for e, lines in pw.advance_view([plain], emp)["by_person"].items()} == {"E1": [3000], "E2": [2000], "E3": [1000]}


# ------------------------------------------------------------------ the check page (static/inboxcheck.js, in Node)
@pytest.fixture
def node_data(off_db):
    if NODE is None:
        pytest.skip("Node is not installed")
    pv = pv_of({"edits": {}, "added": [], "rows": {"5": {"edits": {"0": {"common": 300}}, "added": []}}})
    return {"preview": json.loads(json.dumps(pv)), "accounts": inbox_ai.chart_list(), "vehicles": VEH}


def test_the_table_payload_and_the_count(node_data, tmp_path):
    got = run("""(() => {
      const saved = ickCopy(DATA.preview.line_edits);
      ick.f = DATA; ick.saved = saved; ick.draft = ickCopy(saved);
      const pg = ickPages(DATA).find((p) => p.n === 5);
      const out = { start: ickCount(saved, ick.draft), adv: [pg.adv.rows.length, pg.adv.pair.line] };
      const view = () => ickAdvView(pg, ick.draft);
      ickAdvSet(5, view()[1], "amount", "2,500");
      ickAdvSet(5, view()[1], "amount", "3000");                 // typed back to what was read: no fix
      out.same = ickCount(saved, ick.draft);
      ickAdvSet(5, view()[1], "amount", "2,500");
      ickAdvSet(5, view()[0], "common", "300.00");               // typed back to what is saved
      ickAdvFlag(5, view()[1], "ok", true);
      ickAdvToggle(5, view()[2]);
      const id = ickAdvAdd(5);
      const added = view().find((v) => v.key === `+${id}`);
      ickAdvSet(5, added, "code", "sb-fr009");
      ickAdvSet(5, added, "amount", "4000");
      const gone = ickAdvAdd(5);
      ickAdvToggle(5, view().find((v) => v.key === `+${gone}`));  // added, not saved, removed: nothing left of it
      out.n = ickCount(saved, ick.draft);
      out.payload = ickPayload(ick.draft);
      out.payload.rows["5"].added = out.payload.rows["5"].added.map((a) => ({ ...a, id: /^r[0-9a-z]{1,12}$/.test(a.id) ? "r?" : a.id }));
      out.view = view().map((v) => [v.key.startsWith("+") ? "+" : v.key, v.state, [...v.chg].sort(), v.edited, v.unsure, v.ok]);
      out.sums = ickAdvSums(view());
      out.live = [ickAdvSum(pg, (saved.rows || {})[5]), ickAdvSum(pg, ick.draft.rows[5])];
      out.line = ickLinesOf(pg, ick.draft).find((l) => l.key === "p5:l1").v.amount;
      out.sum = ickPageSum(ickLinesOf(pg, ick.draft));
      return out;
    })()""", node_data, tmp_path)
    assert got["start"] == 0 and got["same"] == 0 and got["adv"] == [4, [3000, 3000, 3000, None]]
    assert got["n"] == 3                                                               # row 2 amount + ok, row 3 deleted, one added
    assert got["payload"] == {"edits": {}, "added": [], "rows": {"5": {
        "edits": {"0": {"common": 300}, "1": {"amount": "2,500", "ok": True}, "2": {"delete": True}},
        "added": [{"id": "r?", "code": "sb-fr009", "name": "", "amount": "4000", "common": "", "fault": "", "other": ""}]}}}
    assert got["view"] == [["0", "row", [], True, False, False], ["1", "row", ["amount"], False, True, True], ["2", "deleted", [], False, False, False],
                           ["3", "row", [], False, False, False], ["+", "new", ["amount", "code", "common", "fault", "name", "other"], False, False, False]]
    assert got["sums"] == {"people": 3, "amount": 9500, "common": 930, "fault": 500, "other": 0}
    assert got["live"] == [9000, 3000 + 2500 + 4000]
    assert got["line"] == 9500 and got["sum"] == 9500                                 # the page's 1500 line follows the table as it is typed


def test_the_table_card_says_where_its_columns_are_used(node_data, tmp_path):
    test = """(() => {
      globalThis.el = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.filter((k) => k), classList: { toggle() {} },
        addEventListener() {}, set textContent(t) { this.attrs.text = t; } });
      globalThis.fill = (n, ...kids) => { n.kids = kids.filter((k) => k); };
      globalThis.fmtMoney = (n) => String(n);
      const words = (n) => [n.attrs.text || ""].concat(...(n.kids || []).map(words));
      const out = {};
      for (const half of ["mid", "end"]) {
        ick.f = { id: "f", preview: { ...DATA.preview, half } };
        ick.saved = ickCopy(ick.f.preview.line_edits); ick.draft = ickCopy(ick.saved); ick.editable = true;
        out[half] = words(ickAdvEl(ickPages(ick.f).find((p) => p.n === 5))).join(" | ");
      }
      return out;
    })()"""
    got = run(test, node_data, tmp_path)
    assert "รายคน: ยอดเบิก / ส่วนกลาง / ความผิด / อุปกรณ์ (แก้ได้ เทียบกับรูป)" in got["mid"]
    assert "ส่วนกลาง / ความผิด / อุปกรณ์ ใช้จากฉบับเซ็นรับในเอกสารปลายเดือน ถ้ามี" in got["mid"] and "ยอดเบิกใช้จากไฟล์กลางเดือน" not in got["mid"]
    assert "ยอดเบิกใช้จากไฟล์กลางเดือน" in got["end"] and "+ เพิ่มคน" in got["mid"] and "ถูกแล้วทั้งตาราง (1 แถวอ่านไม่ชัด)" in got["mid"]
    assert "รวม 3 คน" in got["mid"]


# ------------------------------------------------------------------ review fixes (29 Nov 2026)
# a row whose code and name match no line: "สมชาย ใจดี" (no code) on the line, "SB-FR001 นายสมชาย ใจดีย์" in the table
LOOSE = page("advance", title=TITLE, written_total=6000,
             lines=[line("2026-11-08", "สมชาย ใจดี", 3000, "1500"), line("2026-11-08", "นาย 2 ทดสอบ SB-FR002", 3000, "1500")],
             advance_rows=[arow("SB-FR001", "3000", name="นายสมชาย ใจดีย์"), arow("SB-FR002", "3000")])


def loose_reads():
    rs = reads()
    rs[4] = {**json.loads(json.dumps(LOOSE)), "n": 5}
    return rs


def loose_pv(edits=None):
    return inbox_ai.from_pages(loose_reads(), "กลางเดือน.pdf", {}, line_edits=edits)


def test_a_row_no_line_is_never_books_the_person_twice(off_db):
    p = {**LOOSE, "n": 5}
    assert pw.pair_rows(p) == ([None, 1], [0])
    # a ส่วนกลาง fix, a code fix or ถูกแล้ว on that row: the lines stay as read (6,000), the person once
    for fix in ({"common": 350}, {"code": "SB-FR009"}, {"ok": True}):
        out = pw.edit_page(p, {"edits": {"0": fix}})
        assert [ln["amount"] for ln in out["lines"]] == [3000, 3000], fix
        assert len(pw.page_lines(out)) == 2 and "rows_fixed" not in out
        assert adv_line(loose_pv({"edits": {}, "added": [], "rows": {"5": {"edits": {"0": fix}}}}))["amount"] == 6000
    assert {r["code"]: r["common"] for r in pw.page_rows(pw.edit_page(p, {"edits": {"0": {"common": 350}}}))}["SB-FR001"] == 350
    # the loose line is shown under the table and taken away; the row's ยอดเบิก fixed books it: once, with its code
    pv = loose_pv()
    assert pv["advance_pairs"]["5"] == {"line": [None, 3000], "rest": 3000, "rest_lines": [{"i": 0, "text": "สมชาย ใจดี", "amount": 3000}]}
    fix = {"5": {"edits": {"0": {"amount": 3000}}, "added": [], "drop": [0]}}
    out = pw.edit_page(p, fix["5"])
    assert {ln["code"]: ln["amount"] for ln in pw.page_lines(out)} == {"SB-FR001": 3000, "SB-FR002": 3000}
    booked = loose_pv({"edits": {}, "added": [], "rows": fix})
    assert adv_line(booked)["amount"] == 6000 and check(booked, 5)["status"] == "ok"
    # the amount fixed and the loose line kept: both are booked, and the page's written-total check says so
    both = loose_pv({"edits": {}, "added": [], "rows": {"5": {"edits": {"0": {"amount": 3000}}}}})
    assert adv_line(both)["amount"] == 9000 and check(both, 5)["status"] == "mismatch"


@pytest.mark.parametrize("drop, says", [([1], "ไม่พบบรรทัดที่จะลบ"), ([7], "ไม่พบบรรทัดที่จะลบ"), ([True], "ไม่พบบรรทัดที่จะลบ"),
                                         (["0"], "ไม่พบบรรทัดที่จะลบ"), ("0", "ข้อมูลที่แก้ในตารางรายคนไม่ถูกต้อง")])
def test_only_a_line_no_row_is_can_be_dropped(off_db, drop, says):
    with pytest.raises(ValueError, match=says):
        inbox_ai.clean_line_edits(loose_pv(), {"rows": {"5": {"drop": drop}}}, VEH)


def test_a_dropped_line_is_checked_and_kept_in_order(off_db):
    got = inbox_ai.clean_line_edits(loose_pv(), {"rows": {"5": {"drop": [0, 0]}}}, VEH)
    assert got["rows"] == {"5": {"edits": {}, "added": [], "drop": [0]}}
    end = {**loose_pv(), "half": "end"}
    with pytest.raises(ValueError, match="ฉบับเซ็นรับ"):
        inbox_ai.clean_line_edits(end, {"rows": {"5": {"drop": [0]}}}, VEH)


def test_the_lists_one_line_is_fixed_in_the_table_never_on_the_line(off_db):
    pv = pv_of()
    for e in ({"amount": 5000}, {"delete": True}):
        with pytest.raises(ValueError, match="แก้ที่ตารางรายคน"):
            inbox_ai.clean_line_edits(pv, {"edits": {"p5:l1": e}}, VEH)
    assert inbox_ai.clean_line_edits(pv, {"edits": {"p5:l1": {"text": "เบิกกลางเดือน ช่าง"}}}, VEH)["edits"] == {"p5:l1": {"text": "เบิกกลางเดือน ช่าง"}}
    # page 6 has no table (read before it): its line is fixed as before
    assert inbox_ai.clean_line_edits(pv, {"edits": {"p6:l1": {"amount": 1500}}}, VEH)["edits"] == {"p6:l1": {"amount": 1500.0}}
    # an amount fix saved before this rule: not used, with a flag; sent back unchanged it goes quietly
    old = pv_of({"edits": {"p5:l1": {"amount": 5000, "text": "เบิก"}}, "added": []})
    assert adv_line(old)["amount"] == 9000 and adv_line(old)["text"] == "เบิก"
    assert any("หน้า 5: ยอดของบรรทัดเบิกกลางเดือนแก้ที่ตารางรายคนแทน" in f for f in old["flags"])
    assert inbox_ai.clean_line_edits(old, {"edits": {"p5:l1": {"amount": "5,000", "text": "เบิก"}}}, VEH)["edits"] == {"p5:l1": {"text": "เบิก"}}
    # the pay page reads the same table: its advances are the booked sum
    assert sum(ln["amount"] for ln in pw.page_lines(next(r for r in old["ai_pages"] if r["n"] == 5))) == 9000


def test_a_table_fixed_to_nobody_books_nothing_never_the_written_total(off_db):
    gone = {"5": {"edits": {str(i): {"delete": True} for i in range(3)}, "added": []}}
    pv = pv_of({"edits": {}, "added": [], "rows": gone})
    assert not any(r.get("key") == "p5:l1" for r in pv["batches"][0]["rows"])
    assert any("หน้า 5 (เบิกกลางเดือน): ตารางรายคนที่แก้แล้วไม่มียอดเบิก — ไม่ลงบัญชี" in f for f in pv["flags"])
    assert pw.page_lines(pw.edit_page({**ADV, "n": 5}, gone["5"])) == []
    blank = {"5": {"edits": {str(i): {"amount": ""} for i in range(3)}}}
    assert not any(r.get("key") == "p5:l1" for r in pv_of({"edits": {}, "added": [], "rows": blank})["batches"][0]["rows"])
    # a page whose lines the AI could not read at all still books its written total: a fix that changes no line keeps that
    rs = reads()
    rs[4] = {**rs[4], "lines": []}
    kept = inbox_ai.from_pages(rs, "x.pdf", {}, line_edits={"edits": {}, "added": [], "rows": {"5": {"edits": {"1": {"ok": True}}}}})
    assert adv_line(kept)["amount"] == 9000


def test_who_may_not_see_pay_cannot_change_the_table(db, engine):
    scan_row(db, edits={"edits": {}, "added": [], "rows": FIX})
    got = inbox.get(FID, see_pay=False)
    assert got["can_edit_rows"] is False and inbox.get(FID)["can_edit_rows"] is True
    v0 = got["edits_version"]
    with pytest.raises(inbox.Forbidden, match="เฉพาะผู้มีเมนูรายชื่อพนักงาน"):
        inbox.save_edits(FID, {"edits": {}, "added": [], "rows": {"5": {"edits": {"0": {"common": 999}}}}}, see_pay=False, version=v0)
    with pytest.raises(inbox.Forbidden):
        inbox.save_edits(FID, {"edits": {}, "added": [], "rows": {}}, see_pay=False, version=v0)
    assert db.rows[FID]["preview"]["line_edits"]["rows"] == FIX
    # the line fixes are saved; the table sent back unchanged, or not at all, stays as it was
    out = inbox.save_edits(FID, {"edits": {"p2:l1": {"amount": 450}}, "added": [], "rows": FIX}, see_pay=False, version=v0)
    assert db.rows[FID]["preview"]["line_edits"] == {"edits": {"p2:l1": {"amount": 450.0}}, "added": [], "rows": FIX}
    inbox.save_edits(FID, {"edits": {}, "added": []}, see_pay=False, version=out["edits_version"])
    assert db.rows[FID]["preview"]["line_edits"] == {"edits": {}, "added": [], "rows": FIX} and adv_line(db.rows[FID]["preview"])["amount"] == 9000


@pytest.fixture
def loose_data(off_db):
    if NODE is None:
        pytest.skip("Node is not installed")
    return {"preview": json.loads(json.dumps(loose_pv())), "accounts": inbox_ai.chart_list(), "vehicles": VEH}


def test_the_page_books_a_row_no_line_is_only_when_asked_and_drops_the_loose_line(loose_data, tmp_path):
    got = run("""(() => {
      const saved = ickCopy(DATA.preview.line_edits);
      ick.f = DATA; ick.saved = saved; ick.draft = ickCopy(saved); ick.editable = true;
      const pg = ickPages(DATA).find((p) => p.n === 5);
      const view = () => ickAdvView(pg, ick.draft);
      const out = { start: ickAdvSum(pg, ick.draft.rows[5]), unpaired: view().map((v) => v.unpaired) };
      ickAdvSet(5, view()[0], "common", "350");
      ickAdvFlag(5, view()[0], "ok", true);
      out.ok = ickAdvSum(pg, ick.draft.rows[5]);                  // the person still once: his loose line
      out.loose = ickAdvLoose(pg, ick.draft).map((x) => [x.key, x.state, x.amount]);
      ickAdvBook(5, view()[0], true);
      out.booked = [view()[0].booked, [...view()[0].chg].sort(), ickAdvSum(pg, ick.draft.rows[5])];
      ickAdvSet(5, view()[0], "amount", "3000");                  // typed back to what was read: still booked
      out.kept = view()[0].booked;
      ickAdvDrop(5, 0, true);
      out.dropped = [ickAdvSum(pg, ick.draft.rows[5]), ickAdvLoose(pg, ick.draft).map((x) => [x.state, x.chg])];
      out.payload = ickPayload(ick.draft);
      out.n = ickCount(saved, ick.draft);
      out.line = ickLinesOf(pg, ick.draft).find((l) => l.key === "p5:l1").v.amount;
      ickAdvDrop(5, 0, false); ickAdvBook(5, view()[0], false);
      out.back = [ickAdvSum(pg, ick.draft.rows[5]), JSON.stringify(ick.draft.rows[5].drop || null)];
      return out;
    })()""", loose_data, tmp_path)
    assert got["start"] == 6000 and got["unpaired"] == [True, False]
    assert got["ok"] == 6000 and got["loose"] == [["-0", "loose", 3000]]
    assert got["booked"] == [True, ["amount", "common"], 9000] and got["kept"] is True
    assert got["dropped"] == [6000, [["deleted", True]]]
    assert got["payload"] == {"edits": {}, "added": [], "rows": {"5": {"edits": {"0": {"common": "350", "ok": True, "amount": "3000"}}, "added": [], "drop": [0]}}}
    assert got["n"] == 2 and got["line"] == 6000
    assert got["back"] == [6000, "null"]


def test_the_lists_line_shows_the_table_not_an_old_amount_fix_and_who_cannot_see_pay_reads_only(node_data, tmp_path):
    test = """(() => {
      globalThis.el = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.filter((k) => k), classList: { toggle() {} },
        addEventListener() {}, set textContent(t) { this.attrs.text = t; } });
      globalThis.fill = (n, ...kids) => { n.kids = kids.filter((k) => k); };
      globalThis.fmtMoney = (n) => String(n);
      const words = (n) => [n.attrs.text || ""].concat(...(n.kids || []).map(words));
      const le = { ...DATA.preview.line_edits, edits: { "p5:l1": { amount: 5000 } } };
      ick.f = { ...DATA, can_edit_rows: false }; ick.saved = ickCopy(le); ick.draft = ickCopy(le); ick.editable = true;
      const pg = ickPages(ick.f).find((p) => p.n === 5);
      const l = ickLinesOf(pg, ick.draft).find((x) => x.key === "p5:l1");
      const card = words(ickAdvEl(pg)).join(" | ");
      return { amount: l.v.amount, chg: [...l.chg], ed: ickRowsEd(), card, ed2: (ick.f.can_edit_rows = true, ickRowsEd()) };
    })()"""
    got = run(test, node_data, tmp_path)
    assert got["amount"] == 9000 and got["chg"] == [] and got["ed"] is False and got["ed2"] is True
    assert "แก้ตารางนี้ได้เฉพาะผู้มีเมนูรายชื่อพนักงาน" in got["card"] and "+ เพิ่มคน" not in got["card"]
