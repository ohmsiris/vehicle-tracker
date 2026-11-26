"""กล่องรับไฟล์ phase 2 (AI): app/inbox_ai.py + app/inbox_r_scan.py. A FAKE Anthropic client (never the real API) and a small in-memory
fake of app.supabase (never the real database)."""
import json
import re
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from app import acc_docs, inbox_ai, inbox_r_scan, supabase
from app import inbox_common as ic
from app.config import settings

VEH = [{"id": "v5", "vehicle_number": "5", "plate_number": "1ฒถ 2085", "branch": "สระบุรี"},
       {"id": "v31", "vehicle_number": "31", "plate_number": "84-4607", "branch": "สระบุรี"},
       {"id": "k5", "vehicle_number": "5", "plate_number": "86-8971", "branch": "แก่งคอย"},
       {"id": "k14", "vehicle_number": "14", "plate_number": "1ฒบ 6248", "branch": "แก่งคอย"}]


# ------------------------------------------------------------------ fakes
def page(kind, **kw):
    out = {"page_kind": kind, "title": kw.pop("title", kind), "date_from": None, "date_to": None, "written_total": None,
           "cover_parts": {"expense": 0, "fuel": 0, "advance": 0}, "lines": [], "branch_hints": [], "employee_codes": [],
           "bill": {"party": "", "doc_no": "", "date": "", "period_month": "", "total": None, "what": "", "account": "7900"},
           "summary": "", "person": ""}
    out.update(kw)
    return out


def line(d, text, amount, account="5510", plate=None, number=None, unsure=False):
    return {"date": d, "text": text, "amount": amount, "plate": plate, "truck_number": number, "account": account, "unsure": unsure}


class FakeClient:
    """Answers 'Page n of N' with pages[n-1]; records calls and the most pages read at the same time."""

    def __init__(self, pages, fail=None, delay=0.0):
        self.pages, self.fail, self.delay = pages, fail or {}, delay
        self.calls, self.running, self.most = 0, 0, 0
        self.lock = threading.Lock()
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        text = kw["messages"][0]["content"][1]["text"]
        n = int(re.search(r"Page (\d+) of", text).group(1))
        assert kw["model"] == "claude-opus-5-5" and kw["output_config"]["format"]["type"] == "json_schema"
        assert kw["messages"][0]["content"][0]["type"] == "image"          # one page image per request, never the whole PDF
        with self.lock:
            self.calls += 1
            self.running += 1
            self.most = max(self.most, self.running)
        try:
            time.sleep(self.delay)
            if n in self.fail:
                raise self.fail[n]
            usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=50, cache_read_input_tokens=0)
            return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[SimpleNamespace(type="text", text=json.dumps(self.pages[n - 1]))])
        finally:
            with self.lock:
                self.running -= 1


class FakeDB:
    """Just enough PostgREST for these modules: eq / neq / like / in / is / lte / gte / and(...), cascade acc_docs -> acc_entries."""

    def __init__(self, **tables):
        self.t = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.files = {}

    @staticmethod
    def _ok(row, col, cond):
        op, _, val = cond.partition(".")
        v = row.get(col)
        if op == "not":
            return not FakeDB._ok(row, col, val)
        if op == "is":
            return v is None if val == "null" else str(v).lower() == val
        if op == "eq":
            return str(v) == val
        if op == "neq":
            return str(v) != val
        if op == "in":
            return str(v) in val.strip("()").split(",")
        if op == "like":
            return v is not None and re.fullmatch(re.escape(val).replace(r"\*", ".*"), str(v)) is not None
        if v is None:
            return False
        a, b = (float(v), float(val)) if re.fullmatch(r"-?[\d.]+", str(val)) else (str(v), val)
        return {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[op]

    def _rows(self, table, params):
        out = []
        for r in self.t.get(table, []):
            good = True
            for k, c in (params or {}).items():
                if k in ("select", "order", "limit", "offset"):
                    continue
                if k == "and":
                    for part in c.strip("()").split(","):
                        col, _, cond = part.partition(".")
                        good &= self._ok(r, col, cond)
                else:
                    good &= self._ok(r, k, c)
            if good:
                out.append(r)
        return out

    def select(self, table, params=None):
        rows = [dict(r) for r in self._rows(table, params)]
        return rows[: int(params["limit"])] if params and params.get("limit") else rows

    def select_all(self, table, params, page=1000):
        return self.select(table, params)

    def insert(self, table, rows, **kw):
        out = []
        for r in rows:
            r = dict(r)
            r.setdefault("id", str(uuid.uuid4()))
            self.t.setdefault(table, []).append(r)
            out.append(dict(r))
        return out

    def delete(self, table, match):
        gone = self._rows(table, match)
        ids = {r.get("id") for r in gone}
        self.t[table] = [r for r in self.t.get(table, []) if r not in gone]
        if table == "acc_docs":                                    # acc_entries.doc_id ... on delete cascade
            self.t["acc_entries"] = [e for e in self.t.get("acc_entries", []) if e.get("doc_id") not in ids]
        if table == "acc_entries":
            self.t["acc_lines"] = [ln for ln in self.t.get("acc_lines", []) if ln.get("entry_id") not in ids]

    def storage_upload(self, path, data, content_type, upsert=False, bucket=None):
        assert bucket == supabase.PRIVATE_BUCKET
        self.files[path] = data


@pytest.fixture
def ai_on():
    saved = settings.inbox_ai_enabled
    object.__setattr__(settings, "anthropic_api_key", "test-key")          # conftest puts the real one back after the test
    object.__setattr__(settings, "inbox_ai_enabled", True)
    yield
    object.__setattr__(settings, "inbox_ai_enabled", saved)


@pytest.fixture
def no_db(monkeypatch):
    monkeypatch.setattr(supabase, "configured", lambda: False)


def use_db(monkeypatch, db: FakeDB):
    monkeypatch.setattr(supabase, "configured", lambda: True)
    for name in ("select", "select_all", "insert", "delete", "storage_upload"):
        monkeypatch.setattr(supabase, name, getattr(db, name))
    from app import accounting
    monkeypatch.setattr(accounting, "_require", lambda: None)


def use_client(monkeypatch, client):
    monkeypatch.setattr(inbox_ai, "_client", lambda: client)


def pdf(n):
    import pymupdf
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    return doc.tobytes()


MID_PAGES = [
    page("cover", title="สรุปค่าใช้จ่าย 26 ต.ค. - 10 พ.ย. 69", date_from="2026-10-26", date_to="2026-11-10", written_total=4500,
         cover_parts={"expense": 1500, "fuel": 3000, "advance": 0}, branch_hints=["สาขาเมืองสระบุรี"]),
    page("expense", title="รายการเบิก 26 ต.ค. - 5 พ.ย. 69", written_total=1500, date_from="2026-10-26", date_to="2026-11-05",
         lines=[line("2026-10-26", "ถุงมือ", 500), line(None, "โอที มะเฟือง 3 ชม.", 120, "5410"), line("2026-11-05", "ข้าวพนักงาน", 880, "7900", unsure=True)]),
    page("fuel", title="เบิกค่าน้ำมัน", written_total=3000,
         lines=[line("2026-10-27", "เติมน้ำมัน (05)", 1500, "6130", plate="1ฒถ 2085", number="05"),
                line("2026-11-02", "เติมน้ำมัน (31)", 1500, "6130", plate="84-4607", number="31")]),
    page("memo", title="หนังสือเตือน", summary="หนังสือเตือน 21 ต.ค. 69 — ตีห้า", person="ตีห้า (SB-FR068)", employee_codes=["SB-FR068"]),
]


# ------------------------------------------------------------------ the scan reader: no API call
def test_sniff_is_a_fallback_below_every_exact_reader():
    assert inbox_r_scan.sniff(ic.Dropped(name="a.pdf", data=b"", is_scan=True)) == 0.3
    assert inbox_r_scan.sniff(ic.Dropped(name="a.xlsx", data=b"")) == 0.05
    assert 0.3 < ic.MIN_SNIFF


def test_read_counts_pages_and_gives_the_name_as_a_hint_only(monkeypatch, no_db, ai_on):
    client = FakeClient([])
    use_client(monkeypatch, client)
    f = ic.sniff_file("10-กลางเดือนตุลา69.pdf", pdf(3))
    pv = inbox_r_scan.read(f)
    assert pv["kind"] == "scan" and pv["needs_ai"] is True and pv["pages"] == 3
    assert "อ่านด้วย AI (3 หน้า)" in pv["note"]
    assert pv["month"] is None                                         # a name never decides
    assert "คำใบ้จากชื่อไฟล์" in pv["hint"] and "กลางเดือน" in pv["hint"] and "ต.ค. 2569" in pv["hint"]
    assert all(s["hint"] for s in pv["signals"])
    assert client.calls == 0


def test_read_a_photo_is_one_page_and_an_unknown_workbook_is_stored(no_db):
    jpg = b"\xff\xd8\xff" + b"0" * 100
    assert inbox_r_scan.read(ic.sniff_file("bill.jpg", jpg))["pages"] == 1
    pv = inbox_r_scan.read(ic.Dropped(name="weird.docx", data=b"PK\x03\x04", ext=".docx"))
    assert pv["store_only"] and not pv["needs_ai"]


@pytest.mark.parametrize("name,want", [
    ("10-กลางเดือนตุลา69.pdf", {"half": "mid", "month": "2026-10", "branch": None}),
    ("01-ปลายเดือนมกรา69.pdf", {"half": "end", "month": "2026-01", "branch": None}),
    ("9-ปลายเดือนกันยา 69.pdf", {"half": "end", "month": "2026-09", "branch": None}),
    ("[SRB] รายการเบิกกลางเดือน 15 ตุลาคม69.pdf", {"half": "mid", "month": "2026-10", "branch": "สระบุรี"}),
    ("รายการสรุปค่าใช้จ่ายกลางเดือนมิถุนายน 2569 - สระบุรีวารีเทพ (สาขาเมืองสระบุรี).pdf", {"half": "mid", "month": "2026-06", "branch": "สระบุรี"}),
    ("ค่าใช้จ่ายกลางเดือนมีนาคม69_แก่งคอย.pdf", {"half": "mid", "month": "2026-03", "branch": "แก่งคอย"}),
    ("เอกสาร 140.pdf", {"half": None, "month": None, "branch": None}),
])
def test_name_hints(name, want):
    assert inbox_ai.name_hints(name) == want


def test_company_name_and_province_are_not_a_branch():
    assert inbox_ai.branch_of_text("บริษัท สระบุรีวารีเทพ จำกัด อ.แก่งคอย จ.สระบุรี") == "แก่งคอย"
    assert inbox_ai.branch_of_text("บริษัท สระบุรีวารีเทพ จำกัด") is None
    assert inbox_ai.branch_of_text("[SRB]") == "สระบุรี"


def test_schema_stays_under_the_api_union_limit():
    def count(x):
        if isinstance(x, dict):
            return ("anyOf" in x or isinstance(x.get("type"), list)) + sum(count(v) for v in x.values())
        return sum(count(v) for v in x) if isinstance(x, list) else 0
    assert count(inbox_ai.PAGE_SCHEMA) <= inbox_ai.MAX_UNIONS


# ------------------------------------------------------------------ run(): switches and limits
def test_run_refuses_when_switched_off_or_without_a_key(monkeypatch, no_db, ai_on):
    use_client(monkeypatch, FakeClient(MID_PAGES))
    object.__setattr__(settings, "inbox_ai_enabled", False)
    with pytest.raises(inbox_ai.InboxAIError, match="ปิดอยู่"):
        inbox_ai.run({"file_name": "x.pdf"}, pdf(1))
    object.__setattr__(settings, "inbox_ai_enabled", True)
    object.__setattr__(settings, "anthropic_api_key", "")
    with pytest.raises(inbox_ai.InboxAIError, match="ANTHROPIC_API_KEY"):
        inbox_ai.run({"file_name": "x.pdf"}, pdf(1))


def test_too_many_pages_is_refused_before_any_call(monkeypatch, no_db, ai_on):
    client = FakeClient([])
    use_client(monkeypatch, client)
    monkeypatch.setattr(inbox_ai, "MAX_PAGES", 3)
    with pytest.raises(inbox_ai.InboxAIError, match="ไม่เกิน 3 หน้า"):
        inbox_ai.run({"file_name": "x.pdf"}, pdf(4))
    assert client.calls == 0


def test_pages_are_read_at_most_four_at_a_time_and_tokens_are_summed(monkeypatch, no_db, ai_on):
    pages = MID_PAGES + [page("memo", title=f"m{i}") for i in range(6)]
    client = FakeClient(pages, delay=0.05)
    use_client(monkeypatch, client)
    seen = []
    pv = inbox_ai.run({"file_name": "x.pdf"}, pdf(len(pages)), on_progress=lambda d, n: seen.append((d, n)))
    assert client.calls == 10 and 1 < client.most <= 4
    assert seen[-1] == (10, 10)
    assert pv["ai_usage"] == {"model": "claude-opus-5-5", "calls": 10, "input_tokens": 10 * 1050, "output_tokens": 10 * 200}


def test_a_bad_request_stops_the_run_without_retrying(monkeypatch, no_db, ai_on):
    import anthropic
    import httpx2
    err = anthropic.BadRequestError("too many unions", response=httpx2.Response(400, request=httpx2.Request("POST", "https://x")), body=None)
    client = FakeClient(MID_PAGES, fail={1: err})
    use_client(monkeypatch, client)
    monkeypatch.setattr(inbox_ai, "PARALLEL", 1)
    with pytest.raises(inbox_ai.InboxAIError, match="AI ไม่รับคำขอ"):
        inbox_ai.run({"file_name": "x.pdf"}, pdf(4))
    assert client.calls == 1                                           # no retry, and the other pages were not sent


def test_a_page_that_fails_twice_is_kept_as_an_error_page(monkeypatch, no_db, ai_on):
    client = FakeClient(MID_PAGES, fail={4: RuntimeError("timeout")})
    use_client(monkeypatch, client)
    pv = inbox_ai.run({"file_name": "x.pdf"}, pdf(4))
    assert client.calls == 5                                           # page 4 tried twice
    assert pv["flags"][0].startswith("อ่านไม่สำเร็จ 1 หน้า: 4")
    assert pv["batches"] and pv["ai_usage"]["calls"] == 3              # only answered calls carry tokens


# ------------------------------------------------------------------ an expense document
def test_mid_month_document_becomes_the_acc_docs_batch(monkeypatch, no_db, ai_on):
    use_client(monkeypatch, FakeClient(MID_PAGES))
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    pv = inbox_ai.run({"file_name": "เอกสาร 140.pdf"}, pdf(4))
    assert (pv["month"], pv["branch"], pv["half"]) == ("2026-11", "สระบุรี", "mid")
    assert not pv["conflicts"] and not pv["store_only"] and not pv["needs_ai"]
    b = pv["batches"][0]
    assert b["key"] == "docs:2026-11:สระบุรี:mid" and b["feed"] == "midmonth"
    assert b["file_total"] == 4500 and b["rows_total"] == 4500
    money = [r for r in b["rows"] if r["role"] != "info"]
    assert {r["role"] for r in money} == {"cost", "wages", "unknown"}            # the unsure 7900 line: "should check"
    assert next(r for r in money if r["text"].startswith("โอที"))["date"] == "2026-10-26"     # no date: the line above's
    assert next(r for r in money if "ข้าว" in r["text"])["flag"] == "อ่านไม่ชัด"
    fuel = [e for e in b["entries"] if e["lines"][0]["account"] == "6130"]
    assert [e["lines"][0]["vehicle_id"] for e in fuel] == ["v5", "v31"]
    assert {c["page"]: c["status"] for c in pv["page_checks"]} == {1: "ok", 2: "ok", 3: "ok", 4: "ok"}
    assert any(r["role"] == "info" and "หนังสือเตือน" in r["text"] for r in b["rows"])
    sources = {s["what"]: [] for s in pv["signals"]}
    for s in pv["signals"]:
        sources[s["what"]].append(s["source"])
    assert any("หน้าปก" in x for x in sources["month"]) and any("ทะเบียนรถ" in x for x in sources["branch"])


def test_page_total_that_does_not_add_up_is_flagged(monkeypatch, no_db):
    pages = json.loads(json.dumps(MID_PAGES))
    pages[1]["written_total"] = 1600
    pv = inbox_ai.from_pages([{**p, "n": i} for i, p in enumerate(pages, 1)], "x.pdf", {})
    assert {c["page"]: c["status"] for c in pv["page_checks"]}[2] == "mismatch"
    assert any("อ่านได้ 1,500 เขียนไว้ 1,600" in f for f in pv["flags"])


def test_cover_and_lines_disagree_on_the_month_until_the_owner_picks(monkeypatch, no_db):
    pages = json.loads(json.dumps(MID_PAGES))
    pages[0]["date_to"] = "2026-12-10"                                 # HQ forgot to update the cover
    pages[0]["date_from"] = "2026-11-26"
    reads = [{**p, "n": i} for i, p in enumerate(pages, 1)]
    pv = inbox_ai.from_pages(reads, "x.pdf", {})
    c = next(c for c in pv["conflicts"] if c["key"] == "month")
    assert {o["value"] for o in c["options"]} == {"2026-11", "2026-12"} and "หน้าปก" in c["message"]
    pv2 = inbox_ai.apply_choices(pv, {"month": "2026-11"})             # no new read: rebuilt from the stored pages
    assert pv2["month"] == "2026-11" and not [c for c in pv2["conflicts"] if c["key"] == "month"]
    assert pv2["batches"][0]["key"] == "docs:2026-11:สระบุรี:mid"


def test_branch_on_the_cover_against_the_trucks_is_a_conflict(monkeypatch, no_db):
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    pages = json.loads(json.dumps(MID_PAGES))
    pages[0]["branch_hints"] = ["สาขาแก่งคอย"]
    pages[3]["employee_codes"] = ["SB-FR068", "SB-PD042"]
    pv = inbox_ai.from_pages([{**p, "n": i} for i, p in enumerate(pages, 1)], "x.pdf", {})
    c = next(c for c in pv["conflicts"] if c["key"] == "branch")
    assert c["default"] == "สระบุรี"                                   # trucks 2 + codes 2 outweigh the cover 3
    assert "หน้าปก บอก แก่งคอย" in c["message"]


def test_advance_list_in_the_end_of_month_file_is_not_booked_again(monkeypatch, no_db):
    pages = [page("cover", date_from="2026-11-11", date_to="2026-11-25", written_total=500, cover_parts={"expense": 500, "fuel": 0, "advance": 0},
                  branch_hints=["KK"]),
             page("expense", written_total=500, lines=[line("2026-11-12", "ปะยาง", 500, "6110")]),
             page("advance", title="เบิกกลางเดือน (เซ็นรับ)", lines=[line("2026-11-16", "นาย ก", 3000, "1500")] * 3)]
    pv = inbox_ai.from_pages([{**p, "n": i} for i, p in enumerate(pages, 1)], "x.pdf", {})
    assert pv["half"] == "end" and pv["branch"] == "แก่งคอย"
    b = pv["batches"][0]
    assert b["key"] == "docs:2026-11:แก่งคอย:end" and b["rows_total"] == 500
    assert not any(e["lines"][0]["account"] == "1500" for e in b["entries"])


def test_mid_month_advance_list_is_one_line_per_page_not_a_cost(monkeypatch, no_db):
    pages = [page("advance", title="เบิกกลางเดือน พ.ย. 69", written_total=9000, lines=[line("2026-11-16", f"คน {i}", 3000, "1500") for i in range(3)])]
    pv = inbox_ai.from_pages([{**p, "n": i} for i, p in enumerate(pages, 1)], "10-กลางเดือนพฤศจิกา69 สระบุรี.pdf", {})
    e = pv["batches"][0]["entries"]
    assert len(e) == 1 and e[0]["lines"][0] == {**e[0]["lines"][0], "account": "1500", "debit": 9000.0}
    assert pv["batches"][0]["rows"][0]["role"] == "advance"


# ------------------------------------------------------------------ booking (fake database)
def test_book_replaces_and_unbook_removes_and_images_are_stored(monkeypatch, ai_on):
    db = FakeDB(vehicles=VEH)
    use_db(monkeypatch, db)
    use_client(monkeypatch, FakeClient(MID_PAGES))
    pv = inbox_ai.run({"id": "f1", "file_name": "x.pdf"}, pdf(4))
    assert set(db.files) == {f"accdocs/inbox/f1/p{i}.jpg" for i in range(1, 5)}
    assert not db.t.get("acc_docs")                                    # run() never writes the books
    out = inbox_r_scan.book(json.loads(json.dumps(pv)), "f1")          # as stored in jsonb
    assert out == {"batches": ["docs:2026-11:สระบุรี:mid"], "booked": 4500.0, "rebook": []}
    n_docs, n_entries = len(db.t["acc_docs"]), len(db.t["acc_entries"])
    assert n_entries == 5 and {d["image_path"] for d in db.t["acc_docs"]} == {f"accdocs/inbox/f1/p{i}.jpg" for i in range(1, 5)}
    inbox_r_scan.book(json.loads(json.dumps(pv)), "f1")                # re-drop: replaces, never doubles
    assert (len(db.t["acc_docs"]), len(db.t["acc_entries"])) == (n_docs, n_entries)
    inbox_r_scan.unbook(pv)
    assert not db.t["acc_docs"] and not db.t["acc_entries"]


def test_the_same_half_imported_by_the_script_must_be_replaced_or_kept(monkeypatch):
    old = [{"id": f"d{p}", "batch": "2026-11-saraburi", "feed": "midmonth" if p < 100 else None, "branch": "สระบุรี", "file_name": "mid.pdf",
            "page": p, "kind": "expense", "date_from": "2026-10-26", "date_to": "2026-11-10", "edited_at": None} for p in (2, 3)]
    old += [{"id": "d102", "batch": "2026-11-saraburi", "feed": "endmonth", "branch": "สระบุรี", "file_name": "end.pdf", "page": 102,
             "kind": "expense", "date_from": "2026-11-11", "date_to": "2026-11-25", "edited_at": None}]
    entries = [{"id": "e2", "source_id": "2026-11-saraburi:p2:l1", "doc_id": "d2"}, {"id": "e9", "source_id": "2026-11-saraburi:p102:l1", "doc_id": "d102"}]
    db = FakeDB(vehicles=VEH, acc_docs=old, acc_entries=entries)
    use_db(monkeypatch, db)
    reads = [{**p, "n": i} for i, p in enumerate(MID_PAGES, 1)]
    pv = inbox_ai.from_pages(reads, "x.pdf", {})
    c = next(c for c in pv["conflicts"] if c["key"] == "replace_old")
    assert c["default"] == "keep" and "2026-11-saraburi (mid.pdf, 2 หน้า)" in c["message"]
    with pytest.raises(ValueError):
        inbox_r_scan.book(pv, "f1")                                    # not until the owner picks
    keep = inbox_ai.apply_choices(pv, {"replace_old": "keep"})
    assert keep["store_only"] and inbox_r_scan.book(keep, "f1")["booked"] == 0.0
    rep = inbox_ai.apply_choices(pv, {"replace_old": "replace"})
    assert rep["replace_docs"] == [{"batch": "2026-11-saraburi", "pages": [2, 3]}] and not rep["conflicts"]
    inbox_r_scan.book(rep, "f1")
    batches = {(d["batch"], d["page"]) for d in db.t["acc_docs"]}
    assert ("2026-11-saraburi", 2) not in batches and ("2026-11-saraburi", 102) in batches    # the other half stays
    assert {e["source_id"] for e in db.t["acc_entries"] if not e["source_id"].startswith("docs:")} == {"2026-11-saraburi:p102:l1"}


def test_read_reuses_an_earlier_ai_read_of_the_same_bytes(monkeypatch):
    import hashlib
    data = pdf(4)
    reads = [{**p, "n": i} for i, p in enumerate(MID_PAGES, 1)]
    db = FakeDB(vehicles=VEH, inbox_files=[{"sha256": hashlib.sha256(data).hexdigest(),
                                            "preview": inbox_ai.from_pages(reads, "x.pdf", {"month": "2026-12"})}])
    use_db(monkeypatch, db)
    pv = inbox_r_scan.read(ic.sniff_file("x.pdf", data, {"month": "2026-11"}))
    assert not pv["needs_ai"] and pv["month"] == "2026-11" and pv["batches"][0]["key"] == "docs:2026-11:สระบุรี:mid"


def test_script_import_still_points_pages_at_the_script_images(monkeypatch):
    db = FakeDB(vehicles=VEH)
    use_db(monkeypatch, db)
    b = {"batch": "2026-10-test", "month": "2026-10", "branch": "สระบุรี", "files": {"mid": "mid.pdf"},
         "pages": [{"page": 2, "file": "mid", "file_page": 2, "feed": "midmonth", "kind": "expense", "title": "t", "written_total": 100,
                    "lines": [("26/9", "x", 100, "5510", "ของใช้โรงงาน", {})]}]}
    acc_docs.import_batch(b)
    assert db.t["acc_docs"][0]["image_path"] == "accdocs/2026-10-test/p2.jpg"


# ------------------------------------------------------------------ a single bill, and papers not for the books
WATER = page("bill", title="ใบแจ้งค่าน้ำ", branch_hints=["สาขาแก่งคอย"],
             bill={"party": "โครงการชลประทานสระบุรี", "doc_no": "W-123", "date": "2026-11-03", "period_month": "2026-10", "total": 5320.5,
                   "what": "ค่าน้ำชลประทาน ต.ค. 69", "account": "5200"})


def test_water_bill_is_one_suggested_cost_to_accounts_payable(monkeypatch):
    db = FakeDB(vehicles=VEH)
    use_db(monkeypatch, db)
    pv = inbox_ai.from_pages([{**WATER, "n": 1}], "scan.jpg", {})
    assert (pv["month"], pv["branch"]) == ("2026-10", "แก่งคอย") and not pv["conflicts"]
    b = pv["batches"][0]
    assert b["feed"] == "water_irrigation" and len(b["entries"]) == 1 and b["rows"][0]["role"] == "cost"
    e = b["entries"][0]
    assert e["entry_date"] == "2026-10-25"                             # the bill (3 Nov) is for October: booked to October
    assert [(ln["account"], ln["debit"], ln["credit"]) for ln in e["lines"]] == [("5200", 5320.5, 0.0), ("2100", 0.0, 5320.5)]
    out = inbox_r_scan.book(pv, "f9")
    assert out["booked"] == 5320.5 and out["rebook"] == [{"kind": "cheques", "month": "2026-11"}]    # next month's run pays it
    assert out == {**out, "batches": [b["key"]]}
    n = len(db.t["acc_entries"])
    inbox_r_scan.book(pv, "f9")
    assert len(db.t["acc_entries"]) == n                               # re-drop replaces
    assert inbox_r_scan.unbook(pv) == {"rebook": [{"kind": "cheques", "month": "2026-11"}]}    # the cheque that paid it is the cost again
    assert not db.t["acc_entries"]


def test_bill_already_on_the_expense_list_is_only_kept_as_proof(monkeypatch):
    db = FakeDB(vehicles=VEH, acc_entries=[{"id": "e1", "entry_date": "2026-10-12", "source": "midmonth", "description": "จ่ายค่าน้ำชลประทาน"}],
                acc_lines=[{"id": "l1", "entry_id": "e1", "account": "5200", "debit": 5320.5, "branch": "แก่งคอย"}])
    use_db(monkeypatch, db)
    pv = inbox_ai.from_pages([{**WATER, "n": 1}], "scan.jpg", {})
    assert pv["store_only"] and "ลงไว้แล้ว 12/10/2026" in pv["batches"][0]["rows"][0]["flag"]
    assert pv["batches"][0]["entries"] == []


def test_bill_without_a_branch_asks(no_db):
    w = json.loads(json.dumps(WATER))
    w["branch_hints"] = []
    pv = inbox_ai.from_pages([{**w, "n": 1}], "scan.jpg", {})
    assert [c["key"] for c in pv["conflicts"]] == ["branch"]
    assert inbox_ai.apply_choices(pv, {"branch": "สระบุรี"})["batches"][0]["branch"] == "สระบุรี"


def test_payslips_and_memos_are_stored_not_booked(no_db):
    reads = [{**page("payslip", title="สลิป", date_from="2026-11-01", date_to="2026-11-25", summary="สลิปเงินเดือน พ.ย. 69"), "n": 1},
             {**page("memo", title="ลาออก", summary="หนังสือลาออก"), "n": 2}]
    pv = inbox_ai.from_pages(reads, "x.pdf", {})
    assert pv["store_only"] and not pv["batches"] and pv["month"] == "2026-11"
    assert "สลิปเงินเดือน 1 หน้า" in pv["note"]
    assert inbox_r_scan.book(pv, "x") == {"batches": [], "booked": 0.0, "rebook": []}


def test_book_refuses_a_preview_not_yet_read_by_ai(no_db):
    pv = inbox_r_scan.read(ic.sniff_file("x.pdf", pdf(2)))
    with pytest.raises(ValueError, match="อ่านด้วย AI"):
        inbox_r_scan.book(pv, "x")


# ------------------------------------------------------------------ review fixes (26 Nov 2026)
def test_replace_never_takes_over_next_months_half(monkeypatch):
    def doc(batch, page, feed, d_from, d_to, file_name="mid.pdf"):
        return {"id": f"{batch}-{page}", "batch": batch, "feed": feed, "branch": "สระบุรี", "file_name": file_name, "page": page,
                "kind": "expense", "date_from": d_from, "date_to": d_to, "edited_at": None}
    old = [doc("2026-11-saraburi", 2, "midmonth", "2026-10-26", "2026-11-10"),
           doc("2026-12-saraburi", 5, "midmonth", "2026-11-08", "2026-12-10", "dec-mid.pdf")]     # next month's list starts on the 8th
    entries = [{"id": "n5", "source_id": "2026-12-saraburi:p5:l1", "doc_id": "2026-12-saraburi-5"}]
    db = FakeDB(vehicles=VEH, acc_docs=old, acc_entries=entries)
    use_db(monkeypatch, db)
    reads = [{**p, "n": i} for i, p in enumerate(MID_PAGES, 1)]
    pv = inbox_ai.from_pages(reads, "x.pdf", {})
    c = next(c for c in pv["conflicts"] if c["key"] == "replace_old")
    assert "2026-11-saraburi" in c["message"] and "2026-12-saraburi" not in c["message"]
    rep = inbox_ai.apply_choices(pv, {"replace_old": "replace"})
    assert rep["replace_docs"] == [{"batch": "2026-11-saraburi", "pages": [2]}]
    inbox_r_scan.book(rep, "f1")
    assert ("2026-12-saraburi", 5) in {(d["batch"], d["page"]) for d in db.t["acc_docs"]}             # December stays in the books
    assert any(e["source_id"] == "2026-12-saraburi:p5:l1" for e in db.t["acc_entries"])
    assert inbox_ai.batch_month("docs:2026-11:สระบุรี:mid") == "2026-11" and inbox_ai.batch_month("2026-12-saraburi") == "2026-12"


def test_one_replace_option_per_earlier_batch(monkeypatch):
    old = [{"id": "a2", "batch": "2026-11-saraburi", "feed": "midmonth", "branch": "สระบุรี", "file_name": "mid.pdf", "page": 2, "kind": "expense",
            "date_from": "2026-10-26", "date_to": "2026-11-10", "edited_at": None},
           {"id": "b1", "batch": "docs:2026-11:สระบุรี:mid", "feed": "midmonth", "branch": "สระบุรี", "file_name": "other scan.pdf", "page": 1,
            "kind": "expense", "date_from": "2026-10-26", "date_to": "2026-11-05", "edited_at": "2026-11-20"}]
    use_db(monkeypatch, FakeDB(vehicles=VEH, acc_docs=old))
    pv = inbox_ai.from_pages([{**p, "n": i} for i, p in enumerate(MID_PAGES, 1)], "x.pdf", {})
    c = next(c for c in pv["conflicts"] if c["key"] == "replace_old")
    values = [o["value"] for o in c["options"]]
    assert values == ["replace", "replace:2026-11-saraburi", "replace:docs:2026-11:สระบุรี:mid", "keep"]
    assert "ที่แก้จะหาย" in c["options"][2]["label"] and "ที่แก้จะหาย" not in c["options"][1]["label"]
    one = inbox_ai.apply_choices(pv, {"replace_old": "replace:2026-11-saraburi"})
    assert one["replace_docs"] == [{"batch": "2026-11-saraburi", "pages": [2]}] and not one["conflicts"]
    assert any("ชุดอื่น" in f for f in one["flags"])


def test_rereading_a_booked_ai_bill_keeps_it_booked(monkeypatch):
    db = FakeDB(vehicles=VEH)
    use_db(monkeypatch, db)
    reads = [{**WATER, "n": 1}]
    pv = inbox_ai.from_pages(reads, "scan.jpg", {})
    inbox_r_scan.book(pv, "f9")
    again = inbox_ai.apply_choices(pv, {})                             # อ่านใหม่ / a choice: its own entry is not "already booked"
    assert not again["store_only"] and len(again["batches"][0]["entries"]) == 1 and not again["batches"][0]["rows"][0]["flag"]
    moved = inbox_ai.apply_choices(pv, {"month": "2026-11"})           # booked under another month key: still its own
    assert not moved["store_only"]


def test_same_account_and_amount_alone_is_not_already_booked(monkeypatch):
    db = FakeDB(vehicles=VEH, acc_entries=[{"id": "e1", "entry_date": "2026-10-12", "source": "midmonth", "source_id": "2026-10-kaengkhoi:p3:l2",
                                            "party": "ร้านช่างแดง", "description": "ซ่อมท่อหน้าโรง"}],
                acc_lines=[{"id": "l1", "entry_id": "e1", "account": "5200", "debit": 5320.5, "branch": "แก่งคอย"}])
    use_db(monkeypatch, db)
    pv = inbox_ai.from_pages([{**WATER, "n": 1}], "scan.jpg", {})
    assert not pv["store_only"] and len(pv["batches"][0]["entries"]) == 1
    assert inbox_ai.alike_bill({"party": "x", "doc_no": "W-12345"}, {"description": "จ่ายบิล W12345"})          # the bill number decides


PEA_SCAN = page("bill", title="ใบแจ้งค่าไฟฟ้า", branch_hints=["สาขาเมืองสระบุรี"],
                bill={"party": "การไฟฟ้าส่วนภูมิภาค", "doc_no": "318011438503", "date": "2026-11-05", "period_month": "2026-10",
                      "total": 835380.28, "what": "ค่าไฟฟ้า ต.ค. 69", "account": "5100"})


def test_an_ai_read_pea_bill_is_never_booked(no_db):
    pv = inbox_ai.from_pages([{**PEA_SCAN, "n": 1}], "scan.pdf", {})
    assert pv["store_only"] and "PDF" in pv["note"] and "PEA" in pv["note"]
    assert pv["batches"][0]["entries"] == [] and pv["batches"][0]["rows"][0]["role"] == "info"
    assert inbox_r_scan.book(pv, "f1") == {"batches": [], "booked": 0.0, "rebook": []}
    both = inbox_ai.from_pages([{**PEA_SCAN, "n": 1}, {**WATER, "n": 2, "branch_hints": ["สาขาเมืองสระบุรี"]}], "scan.pdf", {})
    assert not both["store_only"] and [e["source"] for e in both["batches"][0]["entries"]] == ["water_irrigation"]


def test_tokens_spent_are_kept_when_the_read_fails(monkeypatch, no_db, ai_on):
    import anthropic
    import httpx2
    err = anthropic.BadRequestError("bad", response=httpx2.Response(400, request=httpx2.Request("POST", "https://x")), body=None)
    client = FakeClient(MID_PAGES, fail={2: err}, delay=0.02)
    use_client(monkeypatch, client)
    monkeypatch.setattr(inbox_ai, "PARALLEL", 1)
    with pytest.raises(inbox_ai.InboxAIError) as e:
        inbox_ai.run({"file_name": "x.pdf"}, pdf(4))
    assert e.value.usage["calls"] == 1 and e.value.usage["input_tokens"] == 1050 and e.value.usage["model"] == "claude-opus-5-5"
    assert [p["n"] for p in e.value.partial_pages] == [1] and e.value.partial_pages[0]["page_kind"] == "cover"

    use_client(monkeypatch, FakeClient(MID_PAGES))
    monkeypatch.setattr(inbox_ai, "from_pages", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(inbox_ai.InboxAIError, match="สร้างรายการไม่สำเร็จ") as e:
        inbox_ai.run({"file_name": "x.pdf"}, pdf(4))
    assert e.value.usage["calls"] == 4 and len(e.value.partial_pages) == 4
