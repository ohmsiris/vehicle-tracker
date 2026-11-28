"""ตรวจเอกสารก่อนลงบัญชี › บันทึกในพนักงาน (owner, 28 Nov 2026; app/inbox_hr.py): HR pages and staff loans of an end-of-month paper
recorded straight away from the check page. The examples are เอกสาร 341 (สระบุรี, end of Nov 2026). Fakes only: the model's answer is
made up (no network), supabase is a dict in memory -- never the live database."""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from app import employees, hr_suggest, inbox, inbox_ai, inbox_hr, inbox_r_scan, loans, rates, supabase, users
from app import inbox_common as ic
from app.config import settings
from tests.test_inbox_ai import VEH, FakeDB, line, page

FID = "33333333-3333-3333-3333-333333333333"
TODAY = date(2026, 11, 28)


def uid(n):
    return f"00000000-0000-0000-0000-{n:012d}"


PEOPLE = [
    {"id": uid(1), "code": "SB-FR024", "prefix": "นาย", "full_name": "โชอู", "nickname": "", "branch": "สระบุรี", "position": "ผลิต", "status": "active"},
    {"id": uid(2), "code": "SB-PD011", "prefix": "นาย", "full_name": "จำเนียน พิลาแก้ว", "nickname": "เนียน", "branch": "สระบุรี",
     "position": "พนักงานขับรถสาย", "status": "active", "start_date": "2020-01-01"},
    {"id": uid(3), "code": "SB-PD007", "prefix": "นาย", "full_name": "นาวิน", "nickname": "", "branch": "สระบุรี", "position": "พนักงานขับรถสาย", "status": "active"},
    {"id": uid(4), "code": "SB-FR034", "prefix": "นาย", "full_name": "ลามิน", "nickname": "", "branch": "สระบุรี", "position": "ผลิต", "status": "active"},
    {"id": uid(5), "code": "SB-FR076", "prefix": "นาย", "full_name": "แทะมินจอ", "nickname": "แทะมิน", "branch": "สระบุรี", "position": "ผลิต", "status": "active"},
    {"id": uid(6), "code": "SB-FR061", "prefix": "นาย", "full_name": "ตูเรง", "nickname": "", "branch": "สระบุรี", "position": "ผลิต", "status": "resigned",
     "end_date": "2026-11-25"},
    {"id": uid(7), "code": "SB-FR101", "prefix": "นาย", "full_name": "ตู่", "nickname": "", "branch": "สระบุรี", "position": "ผลิต", "status": "resigned",
     "end_date": "2026-11-02"},
    {"id": uid(8), "code": "SB-PD032", "prefix": "นาย", "full_name": "เอก ดีศรี", "nickname": "เอก", "branch": "สระบุรี", "position": "พนักงานขับรถสาย",
     "status": "active"},
    {"id": uid(9), "code": "SB-FR050", "prefix": "นาย", "full_name": "เอกชัย ศรีสุข", "nickname": "ชัย", "branch": "สระบุรี", "position": "ผลิต", "status": "active"},
]
EK_LOAN = {"id": uid(100), "employee_id": uid(8), "kind": "loan", "amount": 10000, "loan_date": "2026-11-21", "repay_mode": "monthly_fixed",
           "repay_amount": 2500, "first_month": "2026-12-01", "status": "active"}
LAMIN_W1 = {"id": uid(200), "employee_id": uid(4), "doc_type": "warning", "doc_date": "2026-08-07", "status": "signed", "warning_level": 1}
RATE_011 = {"id": uid(300), "employee_id": uid(2), "daily_rate": 350, "effective_from": "2026-10-01", "kind": "normal"}
RULE_011 = {"employee_id": uid(2), "pay_group": "DRV-E", "route_until": None}

MEMO = [  # page n -> (title, date, summary, person, codes) as the page read has them
    (14, "บันทึกข้อความ", "2026-11-04", "บันทึกข้อความ 4 พ.ย. 69: ขออนุมัติหักชำระเงินกู้ยืม 5,000 บาท เดือนละ 1,000 บาท 5 เดือน นายโชอู (SB-FR024)", "โชอู (SB-FR024)", ["SB-FR024"]),
    (15, "บันทึกข้อความ", "2026-11-20", "บันทึกข้อความ 20 พ.ย. 69: นายจำเนียน พิลาแก้ว (SB-PD011) จากพนักงานขับรถสายเป็นพนักงานขับรถหน้าโรง วันละ 400 บาท มีผล 18 พ.ย. 69",
     "จำเนียน พิลาแก้ว (SB-PD011)", ["SB-PD011"]),
    (16, "หนังสือเตือน", "2026-11-25", "หนังสือเตือน 25 พ.ย. 69: นาวิน (SB-PD007) สแกนนิ้วขาเดียว 11 วัน ไม่สแกนเลย 3 วัน อนุมัติวันที่ขาด 14 วัน ไม่อนุมัติเบี้ยขยัน",
     "นาวิน (SB-PD007)", ["SB-PD007"]),
    (17, "หนังสือเตือน", "2026-11-23", "หนังสือเตือน 23 พ.ย. 69: ลามิน (SB-FR034) หยุดงานโดยไม่แจ้ง หักเงิน 1,000 บาท วันที่ 1/12/69", "ลามิน (SB-FR034)", ["SB-FR034"]),
    (18, "หนังสือเตือน", "2026-10-29", "หนังสือเตือน 29 ต.ค. 69: แทะมิน (SB-FR076) ไม่ฟังคำสั่ง", "แทะมิน (SB-FR076)", ["SB-FR076"]),
    (19, "สัญญาประนีประนอมยอมความ", "2026-11-15", "สัญญาประนีประนอมยอมความ วิริยะประกันภัย 5,000 บาท", "", []),
    (20, "หนังสือลาออก", "2026-11-20", "หนังสือลาออก ตูเรง SB-FR061 วันสุดท้าย 25/11/69", "ตูเรง (SB-FR061)", ["SB-FR061"]),
    (21, "หนังสือลาออก", "2026-06-01", "หนังสือลาออก ตู่ SB-FR101 วันสุดท้าย 2 มิ.ย. 69", "ตู่ (SB-FR101)", ["SB-FR101"]),
]


def reads():
    out = [{**page("cover", title="สรุปค่าใช้จ่าย 11-25 พ.ย. 69", date_from="2026-11-11", date_to="2026-11-25", written_total=10500,
                   cover_parts={"expense": 10500, "fuel": 0, "advance": 0}, branch_hints=["สาขาเมืองสระบุรี"]), "n": 1},
           {**page("expense", title="รายการเบิก 11-25 พ.ย. 69", written_total=10500, date_from="2026-11-11", date_to="2026-11-25",
                   lines=[line("2026-11-12", "ถุงมือ", 500), line("2026-11-19", "เอก ลีศรี กู้เงินโรงงาน (2500x4)", 10000, "1510")]), "n": 2}]
    for n, title, d, summary, person, codes in MEMO:
        out.append({**page("memo", title=title, date_from=d, summary=summary, person=person, employee_codes=codes), "n": n})
    return out


def ai_item(**kw):
    """One item of the model's answer: every key there, as the schema forces."""
    base = {"source": "page", "page": 0, "line_key": "", "kind": "warning", "employee_code": "", "employee_name": "", "summary": "",
            "doc_date": "", "offence_date": "", "offence": "", "punishment": "", "penalty_amount": 0, "level": 0, "end_date": "", "reason": "",
            "loan_kind": "loan", "amount": 0, "loan_date": "", "repay_amount": 0, "first_month": "", "change_date": "", "to_position": "",
            "to_branch": "", "daily_rate": 0}
    return {**base, **kw}


ANSWER = [
    ai_item(page=14, kind="loan", employee_code="SB-FR024", employee_name="โชอู", summary="เงินกู้ 5,000 หักเดือนละ 1,000", amount=5000,
            loan_date="2026-11-04", repay_amount=1000, reason="ขออนุมัติหักชำระเงินกู้ยืม"),
    ai_item(page=15, kind="role_change", employee_code="SB-PD011", employee_name="จำเนียน พิลาแก้ว", summary="ย้ายไปขับรถหน้าโรง วันละ 400",
            change_date="2026-11-18", to_position="พนักงานขับรถหน้าโรง", daily_rate=400, reason="จากพนักงานขับรถสายเป็นพนักงานขับรถหน้าโรง"),
    ai_item(page=16, kind="warning", employee_code="SB-PD007", employee_name="นาวิน", summary="สแกนนิ้วไม่ครบ", doc_date="2026-11-25",
            offence="สแกนนิ้วขาเดียว 11 วัน ไม่สแกนเลย 3 วัน", punishment="ไม่อนุมัติเบี้ยขยัน"),
    ai_item(page=17, kind="warning", employee_code="SB-FR034", employee_name="ลามิน", summary="หยุดงานโดยไม่แจ้ง", doc_date="2026-11-23",
            offence="หยุดงานโดยไม่แจ้ง", punishment="หักเงิน 1,000 บาท วันที่ 1/12/69", penalty_amount=1000, level=2),
    ai_item(page=18, kind="warning", employee_code="SB-FR076", employee_name="แทะมิน", summary="ไม่ฟังคำสั่ง", doc_date="2026-10-29", offence="ไม่ฟังคำสั่ง"),
    ai_item(page=20, kind="resign", employee_code="SB-FR061", employee_name="ตูเรง", summary="ลาออก", end_date="2026-11-25"),
    ai_item(page=21, kind="resign", employee_code="SB-FR101", employee_name="ตู่", summary="ลาออก", end_date="2026-06-02"),
    ai_item(source="line", page=2, line_key="p2:l2", kind="loan", employee_name="เอก ลีศรี", summary="กู้เงินโรงงาน 2,500 x 4", amount=10000,
            loan_date="2026-11-19", repay_amount=2500),
    # what the code itself throws away: a page that was not sent, a line that is not there, a kind it does not know
    ai_item(page=99, kind="warning", employee_code="SB-FR024"),
    ai_item(source="line", page=2, line_key="p2:l9", kind="loan", amount=1),
    ai_item(page=19, kind="insurance"),
]


class HrClient:
    """Answers the text-only HR call with `answer`; a page-image call (inbox_ai.read_page) with pages[n-1]."""

    def __init__(self, answer, pages=None):
        self.answer, self.pages, self.hr_calls, self.sent = answer, pages or [], 0, None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        content = kw["messages"][0]["content"]
        assert kw["model"] == "claude-opus-5-5" and kw["output_config"]["format"]["type"] == "json_schema"
        usage = SimpleNamespace(input_tokens=300, output_tokens=100, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        if content[0]["type"] == "image":                     # a page read
            n = int(content[1]["text"].split()[1])
            return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[SimpleNamespace(type="text", text=json.dumps(self.pages[n - 1]))])
        assert all(b["type"] == "text" for b in content)       # the HR call never sends a picture
        self.hr_calls += 1
        self.sent = json.loads(content[0]["text"])
        return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[SimpleNamespace(type="text", text=json.dumps({"items": self.answer}))])


class DB(FakeDB):
    """test_inbox_ai's in-memory PostgREST plus update and upsert (on_conflict) and the drop box's rows."""

    def update(self, table, match, values):
        out = []
        for r in self._rows(table, match):
            r.update(values)
            out.append(dict(r))
        return out

    def insert(self, table, rows, *, on_conflict=None, ignore_duplicates=False):
        if table == "hr_suggestions" and self.refuse_role_change and any(r["kind"] == "role_change" for r in rows):
            raise supabase.SupabaseError("violates check constraint hr_suggestions_kind_check")
        if table == "employee_loans":                                       # the column default
            rows = [{"status": "active", **r} for r in rows]
        if on_conflict:
            cols = on_conflict.split(",")
            out = []
            for r in rows:
                have = next((x for x in self.t.get(table, []) if all(x.get(c) == r.get(c) for c in cols)), None)
                if have:
                    have.update(r)
                    out.append(dict(have))
                else:
                    out += super().insert(table, [r])
            return out
        return super().insert(table, rows)

    refuse_role_change = False


@pytest.fixture
def pv(monkeypatch):
    monkeypatch.setattr(supabase, "configured", lambda: False)
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    monkeypatch.setattr(inbox_ai, "_cash_fuel_lines", lambda m, b, h: [])
    monkeypatch.setattr(inbox_ai, "_earlier_docs", lambda *a: [])
    return inbox_ai.from_pages(reads(), "เอกสาร 341.pdf", {})


@pytest.fixture
def db(monkeypatch, pv):
    d = DB(employees=[dict(p) for p in PEOPLE], employee_loans=[dict(EK_LOAN)], employee_documents=[dict(LAMIN_W1)], employee_rates=[dict(RATE_011)],
           employee_pay_rules=[dict(RULE_011)], hr_suggestions=[], inbox_files=[], drivers=[], employee_role_changes=[], employee_loan_entries=[])
    for name in ("select", "select_all", "insert", "update", "delete"):
        monkeypatch.setattr(supabase, name, getattr(d, name))
    monkeypatch.setattr(supabase, "configured", lambda: True)
    monkeypatch.setattr(employees, "today_th", lambda: TODAY)
    monkeypatch.setattr(employees, "sync_driver", lambda row: None)
    for mod in (employees, loans, rates):
        monkeypatch.setitem(mod._probe, "at", 1e18)
        monkeypatch.setitem(mod._probe, "ok", True)
    return d


@pytest.fixture
def client_hr(monkeypatch):
    c = HrClient(ANSWER, pages=[{k: v for k, v in r.items() if k != "n"} for r in reads()])
    monkeypatch.setattr(inbox_ai, "_client", lambda: c)
    return c


def ai_on():
    object.__setattr__(settings, "anthropic_api_key", "test-key")            # conftest puts the real one back after the test
    object.__setattr__(settings, "inbox_ai_enabled", True)


def file_row(db, preview, status="read", **extra):
    row = {"id": FID, "file_name": "เอกสาร 341.pdf", "kind": "scan", "status": status, "storage_path": f"inbox/{FID}.pdf", "mime_type": "application/pdf",
           "batches": [], "choices": {}, "branches": ["สระบุรี"], "sha256": "s341", "ai_usage": {"model": "m", "calls": 21, "input_tokens": 1, "output_tokens": 1},
           "month": "2026-11", "preview": json.loads(json.dumps(preview)), **extra}
    db.t["inbox_files"] = [row]
    return row


def items_of(db):
    return {it["id"]: it for it in db.t["inbox_files"][0]["preview"]["hr_items"]}


def hr_view(access="ok"):
    return {it["id"]: it for it in inbox.get(FID, True, access)["hr"]["items"]}


@pytest.fixture
def read(db, pv, client_hr):
    """The file as stored after a paid read: the HR items made by the one call."""
    items, used = inbox_hr.read_items(pv)
    file_row(db, {**pv, "hr_items": items})
    return items, used


# ------------------------------------------------------------------ making the items
def test_the_call_reads_only_text_and_the_items_come_from_the_real_example(db, pv, client_hr):
    src = inbox_hr.sources(pv)
    assert [p["page"] for p in src["pages"]] == [14, 15, 16, 17, 18, 19, 20, 21]          # the insurance page goes too: the model skips it
    assert src["lines"] == [{"key": "p2:l2", "page": 2, "date": "2026-11-19", "text": "เอก ลีศรี กู้เงินโรงงาน (2500x4)", "amount": 10000.0}]
    items, used = inbox_hr.read_items(pv)
    assert client_hr.hr_calls == 1 and client_hr.sent == src
    assert used == {"model": "claude-opus-5-5", "calls": 1, "input_tokens": 300, "output_tokens": 100}
    got = {it["id"]: (it["kind"], it["employee_code"], it["page"]) for it in items}
    assert got == {"p14:loan:SB-FR024": ("loan", "SB-FR024", 14), "p15:role_change:SB-PD011": ("role_change", "SB-PD011", 15),
                   "p16:warning:SB-PD007": ("warning", "SB-PD007", 16), "p17:warning:SB-FR034": ("warning", "SB-FR034", 17),
                   "p18:warning:SB-FR076": ("warning", "SB-FR076", 18), "p20:resign:SB-FR061": ("resign", "SB-FR061", 20),
                   "p21:resign:SB-FR101": ("resign", "SB-FR101", 21), "p2:l2:loan": ("loan", "SB-PD032", 2)}
    by = {it["id"]: it for it in items}
    assert by["p14:loan:SB-FR024"]["fields"] == {"loan_kind": "loan", "amount": 5000.0, "loan_date": "2026-11-04", "repay_amount": 1000.0,
                                                  "first_month": "2026-12-01", "reason": "ขออนุมัติหักชำระเงินกู้ยืม"}
    assert by["p15:role_change:SB-PD011"]["fields"] == {"change_date": "2026-11-18", "to_position": "พนักงานขับรถหน้าโรง", "to_branch": None,
                                                         "daily_rate": 400.0, "reason": "จากพนักงานขับรถสายเป็นพนักงานขับรถหน้าโรง"}
    assert by["p17:warning:SB-FR034"]["fields"]["penalty_amount"] == 1000.0 and by["p17:warning:SB-FR034"]["fields"]["level"] == 2
    assert by["p16:warning:SB-PD007"]["fields"]["penalty_amount"] is None and by["p16:warning:SB-PD007"]["fields"]["level"] is None
    ek = by["p2:l2:loan"]
    assert (ek["source"], ek["line_key"], ek["match"], ek["employee_name_read"]) == ("line", "p2:l2", "name", "เอก ลีศรี")
    assert all(it["status"] == "pending" for it in items)


def test_no_hr_page_and_no_loan_line_means_no_call(monkeypatch, client_hr):
    monkeypatch.setattr(supabase, "configured", lambda: False)
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    plain = inbox_ai.from_pages([{**r, "n": i} for i, r in enumerate(reads()[:1], 1)], "x.pdf", {})
    assert inbox_hr.read_items(plain) == ([], {}) and client_hr.hr_calls == 0


@pytest.mark.parametrize("code, name, want", [
    ("SB-FR024", "", "SB-FR024"),
    ("sb fr 024", "", "SB-FR024"),                 # as a clerk writes it
    ("", "เอก ลีศรี", "SB-PD032"),                   # misspelt surname: still him (consonants)
    ("", "นายจำเนียน พิลาแก้ว", "SB-PD011"),
    ("", "แทะมิน", "SB-FR076"),                     # the nickname
    ("SB-FR999", "ตู่", "SB-FR101"),               # a code nobody has: the name decides
    ("", "เอ", None),                              # too little to go on
    ("", "สมศักดิ์ มั่นคง", None),                    # nobody like it
])
def test_who_an_item_is_about(code, name, want):
    emp, how = inbox_hr.resolve(code, name, PEOPLE)
    assert (emp["code"] if emp else None) == want
    assert how == (None if want is None else ("code" if "024" in code else "name"))


def test_two_people_equally_like_the_name_is_nobody():
    twins = PEOPLE + [{**PEOPLE[7], "id": uid(10), "code": "SB-PD099", "full_name": "เอก ดีศรี", "nickname": ""}]
    assert inbox_hr.resolve("", "เอก ลีศรี", twins) == (None, None)


# ------------------------------------------------------------------ what is there now, and where it goes
def test_what_is_there_now_for_each_example(read):
    v = hr_view()
    assert v["p14:loan:SB-FR024"]["dest"] == "พนักงาน · นายโชอู (SB-FR024) · เงินกู้ 5,000 ผ่อน 1,000/เดือน"
    assert v["p14:loan:SB-FR024"]["now"] == "ยังไม่มีเงินกู้ที่ผ่อนอยู่" and v["p14:loan:SB-FR024"]["block"] is None
    rc = v["p15:role_change:SB-PD011"]
    assert rc["dest"] == "พนักงาน · นายจำเนียน พิลาแก้ว (SB-PD011) · เปลี่ยนตำแหน่ง → พนักงานขับรถหน้าโรง 400/วัน จาก 18/11/2569"
    assert rc["now"] == ("ตอนนี้: พนักงานขับรถสาย สาขาสระบุรี · ค่าแรง 350/วัน (ตั้งแต่ 01/10/2569) · กลุ่มคิดเงิน DRV-E"
                         " · ยังไม่ได้ตั้งวันสุดท้ายที่ขับรถสาย")
    nw = v["p16:warning:SB-PD007"]
    assert nw["dest"] == "พนักงาน · นายนาวิน (SB-PD007) · หนังสือเตือนครั้งที่ 1" and nw["now"] == "ยังไม่มีหนังสือเตือนในระบบ · ฉบับนี้จะเป็นครั้งที่ 1"
    lm = v["p17:warning:SB-FR034"]
    assert lm["dest"] == "พนักงาน · นายลามิน (SB-FR034) · หนังสือเตือนครั้งที่ 2"
    assert lm["now"] == "หนังสือเตือนที่มีแล้ว: ครั้งที่ 1 (07/08/2569) · ฉบับนี้จะเป็นครั้งที่ 2" and lm["block"] is None
    tr = v["p20:resign:SB-FR061"]
    assert tr["now"] == "ลาออกแล้ว วันสุดท้าย 25/11/2569 — มีแล้ว ตรงกัน" and tr["block"] == "มีแล้ว ตรงกัน — ไม่มีอะไรต้องบันทึก"
    assert tr["dest"] == "พนักงาน · นายตูเรง (SB-FR061) · ลาออก วันสุดท้าย 25/11/2569"
    tu = v["p21:resign:SB-FR101"]
    assert tu["now"].startswith("ลาออกแล้ว วันสุดท้าย 02/11/2569 — ในกระดาษ 02/06/2569 ไม่ตรงกัน") and tu["block"] is None
    assert tu["dest"] == "พนักงาน · นายตู่ (SB-FR101) · แก้วันสุดท้ายเป็น 02/06/2569"
    ek = v["p2:l2:loan"]
    assert ek["now"] == "เงินกู้ที่ผ่อนอยู่: เงินกู้ทั่วไป 10,000 ผ่อน 2,500/เดือน เริ่ม 12/2569 (กู้ 21/11/2569) — มีแล้ว (ยอดเดียวกัน ไม่บันทึกซ้ำ)"
    assert ek["block"] == "มีเงินกู้นี้แล้ว" and ek["employee"]["code"] == "SB-PD032"
    out = inbox.get(FID, True, "ok")["hr"]
    assert out["pending"] == 8 and out["read"] and not out["can_read"] and "พนักงานขับรถหน้าโรง" in out["options"]["positions"]


def test_without_the_hr_menu_nothing_of_the_system_is_shown(read):
    for access, why in (("none", inbox_hr.NO_ACCESS), ("locked", "ใส่ PIN ส่วนพนักงานก่อน")):
        out = inbox.get(FID, True, access)["hr"]
        assert out["access"] == access and "options" not in out
        assert all(it["now"] is None and it["block"] == why and it["employee"] is None for it in out["items"])


def test_an_unresolved_person_asks_to_pick(db, pv):
    items = inbox_hr.build_items([ai_item(page=18, kind="warning", employee_name="ใครก็ไม่รู้", doc_date="2026-10-29", offence="x")],
                                 inbox_hr.sources(pv), PEOPLE)
    file_row(db, {**pv, "hr_items": items})
    it = hr_view()["p18:warning:x"]
    assert it["employee_code"] is None and it["block"] == "เลือกพนักงานก่อน" and it["dest"] == "พนักงาน · ยังไม่ได้เลือกคน (ในกระดาษ: ใครก็ไม่รู้)"
    inbox_hr.set_person(FID, "p18:warning:x", "sb-fr076")
    it = hr_view()["p18:warning:x"]
    assert it["employee_code"] == "SB-FR076" and it["block"] is None and it["dest"].endswith("หนังสือเตือนครั้งที่ 1")


# ------------------------------------------------------------------ recording
def test_a_loan_memo_is_recorded_once(db, read):
    inbox_hr.apply(FID, "p14:loan:SB-FR024", {}, "เจ้าของ")
    new = [ln for ln in db.t["employee_loans"] if ln["employee_id"] == uid(1)]
    assert len(new) == 1 and (new[0]["amount"], new[0]["repay_mode"], new[0]["repay_amount"], new[0]["first_month"]) == (5000.0, "monthly_fixed", 1000.0, "2026-12-01")
    assert new[0]["memo_ref"] == "เอกสาร 341.pdf หน้า 14"
    it = items_of(db)["p14:loan:SB-FR024"]
    assert it["status"] == "applied" and it["result"] == "นายโชอู (SB-FR024) · เงินกู้ 5,000 ผ่อน 1,000/เดือน เริ่ม 12/2569" and it["applied_by"] == "เจ้าของ"
    with pytest.raises(inbox.InboxError, match="บันทึกซ้ำไม่ได้"):
        inbox_hr.apply(FID, "p14:loan:SB-FR024", {})
    assert len([ln for ln in db.t["employee_loans"] if ln["employee_id"] == uid(1)]) == 1
    hs = db.t["hr_suggestions"]
    assert [(h["batch"], h["page"], h["kind"], h["employee_code"], h["status"]) for h in hs] == [("docs:2026-11:สระบุรี:end", 14, "loan", "SB-FR024", "applied")]
    assert hr_view()["p14:loan:SB-FR024"]["block"] == "บันทึกแล้ว"


def test_eks_loan_is_already_there_and_is_never_a_second_one(db, read):
    with pytest.raises(inbox.InboxError, match="มีเงินกู้นี้แล้ว"):
        inbox_hr.apply(FID, "p2:l2:loan", {})
    assert len(db.t["employee_loans"]) == 1
    inbox_hr.dismiss(FID, "p2:l2:loan")
    assert items_of(db)["p2:l2:loan"]["status"] == "dismissed" and db.t["hr_suggestions"][0]["status"] == "dismissed"
    inbox_hr.reopen(FID, "p2:l2:loan")
    assert items_of(db)["p2:l2:loan"]["status"] == "pending" and db.t["hr_suggestions"] == []


def test_warnings_get_their_level_and_the_fine(db, read):
    inbox_hr.apply(FID, "p17:warning:SB-FR034", {})
    w = [d for d in db.t["employee_documents"] if d["employee_id"] == uid(4) and d["doc_date"] == "2026-11-23"][0]
    assert (w["warning_level"], w["status"], w["content"]["penalty_amount"], w["content"]["offence"]) == (2, "signed", 1000.0, "หยุดงานโดยไม่แจ้ง")
    assert items_of(db)["p17:warning:SB-FR034"]["result"] == "นายลามิน (SB-FR034) · หนังสือเตือนครั้งที่ 2 (เซ็นแล้ว) · หักเงิน 1,000 (ลงวันที่ 23/11/2569)"
    inbox_hr.apply(FID, "p16:warning:SB-PD007", {"fields": {"punishment": "ไม่อนุมัติเบี้ยขยัน (งวด พ.ย.)"}})
    n = [d for d in db.t["employee_documents"] if d["employee_id"] == uid(3)][0]
    assert n["warning_level"] == 1 and n["content"]["punishment"] == "ไม่อนุมัติเบี้ยขยัน (งวด พ.ย.)" and n["content"]["penalty_amount"] is None
    # a second warning of the same day is refused
    db.t["inbox_files"][0]["preview"]["hr_items"].append({**items_of(db)["p16:warning:SB-PD007"], "id": "p16:warning:again", "status": "pending"})
    with pytest.raises(inbox.InboxError, match="มีหนังสือเตือนวันที่ 25/11/2569 แล้ว"):
        inbox_hr.apply(FID, "p16:warning:again", {})


def test_a_resignation_already_there_is_not_written_and_a_different_day_is_the_owners_call(db, read):
    with pytest.raises(inbox.InboxError, match="มีแล้ว ตรงกัน"):
        inbox_hr.apply(FID, "p20:resign:SB-FR061", {})
    with pytest.raises(inbox.InboxError, match="วันสุดท้ายที่ทำงานไม่ถูกต้อง"):
        inbox_hr.apply(FID, "p21:resign:SB-FR101", {"fields": {"end_date": "2 มิ.ย."}})
    with pytest.raises(inbox.InboxError, match="มีแล้ว ตรงกัน"):            # the owner reads 02/11 too: nothing to change
        inbox_hr.apply(FID, "p21:resign:SB-FR101", {"fields": {"end_date": "2026-11-02"}})
    inbox_hr.apply(FID, "p21:resign:SB-FR101", {})                          # the paper's day: the system's is corrected
    assert next(p for p in db.t["employees"] if p["code"] == "SB-FR101")["end_date"] == "2026-06-02"
    assert items_of(db)["p21:resign:SB-FR101"]["result"] == "นายตู่ (SB-FR101) · แก้วันสุดท้ายเป็น 02/06/2569"


def test_a_resignation_of_someone_still_working(db, pv):
    items = inbox_hr.build_items([ai_item(page=20, kind="resign", employee_code="SB-FR024", end_date="2026-11-30", reason="กลับบ้าน")],
                                 inbox_hr.sources(pv), PEOPLE)
    file_row(db, {**pv, "hr_items": items})
    assert hr_view()["p20:resign:SB-FR024"]["now"] == "ยังทำงานอยู่ · ผลิต สาขาสระบุรี"
    inbox_hr.apply(FID, "p20:resign:SB-FR024", {})
    p = next(p for p in db.t["employees"] if p["code"] == "SB-FR024")
    assert (p["status"], p["end_date"], p["end_reason"]) == ("resigned", "2026-11-30", "กลับบ้าน")


def test_a_role_change_writes_the_change_and_the_rate_payroll_reads(db, read):
    db.refuse_role_change = True                                            # schema_v95.sql not applied yet: still recorded
    inbox_hr.apply(FID, "p15:role_change:SB-PD011", {})
    ch = db.t["employee_role_changes"]
    assert [(c["from_position"], c["to_position"], c["change_date"]) for c in ch] == [("พนักงานขับรถสาย", "พนักงานขับรถหน้าโรง", "2026-11-18")]
    assert next(p for p in db.t["employees"] if p["code"] == "SB-PD011")["position"] == "พนักงานขับรถหน้าโรง"
    new = [r for r in db.t["employee_rates"] if r["employee_id"] == uid(2) and r["effective_from"] == "2026-11-18"]
    assert len(new) == 1 and new[0]["daily_rate"] == 400.0 and new[0]["memo_ref"] == "เอกสาร 341.pdf หน้า 15"
    from app.payroll import rate_spans
    assert rate_spans(db.t["employee_rates"], date(2026, 10, 26), date(2026, 11, 25)) == [(350.0, date(2026, 10, 26), date(2026, 11, 17)),
                                                                                       (400.0, date(2026, 11, 18), date(2026, 11, 25))]
    it = items_of(db)["p15:role_change:SB-PD011"]
    assert it["status"] == "applied" and it["result"].startswith("นายจำเนียน พิลาแก้ว (SB-PD011) · เปลี่ยนตำแหน่ง → พนักงานขับรถหน้าโรง จาก 18/11/2569 · ค่าแรง 400/วัน")
    assert db.t["hr_suggestions"] == []                                     # the row the old constraint refused; nothing else lost
    db.refuse_role_change = False
    db.t["inbox_files"][0]["preview"]["hr_items"].append({**it, "id": "p15:again", "status": "pending"})
    with pytest.raises(inbox.InboxError, match="เปลี่ยนแล้ว"):
        inbox_hr.apply(FID, "p15:again", {})


def test_loan_terms_change_the_loan_it_finds(db, pv):
    items = inbox_hr.build_items([ai_item(page=14, kind="loan_terms", employee_code="SB-PD032", repay_amount=2000, first_month="2027-01-01")],
                                 inbox_hr.sources(pv), PEOPLE)
    file_row(db, {**pv, "hr_items": items})
    assert hr_view()["p14:loan_terms:SB-PD032"]["now"].startswith("เงินกู้ที่จะเปลี่ยน: เงินกู้ทั่วไป 10,000 ผ่อน 2,500/เดือน")
    inbox_hr.apply(FID, "p14:loan_terms:SB-PD032", {})
    assert (db.t["employee_loans"][0]["repay_amount"], db.t["employee_loans"][0]["first_month"]) == (2000.0, "2027-01-01")


@pytest.mark.parametrize("item, fields, says", [
    ("p14:loan:SB-FR024", {"amount": "abc"}, "ยอดเงินกู้ไม่ถูกต้อง"),
    ("p14:loan:SB-FR024", {"repay_amount": 6000}, "ไม่เกิน 5,000"),
    ("p14:loan:SB-FR024", {"loan_date": "2026-12-25"}, "อนาคต"),
    ("p14:loan:SB-FR024", {"loan_kind": "gift"}, "ประเภทเงินกู้"),
    ("p14:loan:SB-FR024", {"first_month": "2026-10"}, "ไม่ก่อนเดือนที่กู้"),
    ("p14:loan:SB-FR024", {"party": "x"}, "แก้ช่อง party ไม่ได้"),
    ("p17:warning:SB-FR034", {"level": 5}, "1–3"),
    ("p17:warning:SB-FR034", {"offence": " "}, "ใส่ความผิด"),
    ("p17:warning:SB-FR034", {"penalty_amount": -1}, "มากกว่า 0"),
    ("p15:role_change:SB-PD011", {"to_position": "ผู้จัดการใหญ่"}, "เลือกตำแหน่งใหม่"),
    ("p15:role_change:SB-PD011", {"daily_rate": 9000}, "ไม่เกิน 5,000"),
    ("p15:role_change:SB-PD011", {"change_date": "2027-06-01"}, "ไกลเกินไป"),
])
def test_what_the_owner_sends_is_checked_strictly(db, read, item, fields, says):
    before = json.loads(json.dumps(db.t))
    with pytest.raises(inbox.InboxError, match=says):
        inbox_hr.apply(FID, item, {"fields": fields})
    assert db.t == before


def test_apply_to_someone_else_by_picking_them(db, read):
    inbox_hr.apply(FID, "p18:warning:SB-FR076", {"employee_code": "SB-FR024"})
    assert [d["employee_id"] for d in db.t["employee_documents"] if d["doc_date"] == "2026-10-29"] == [uid(1)]
    with pytest.raises(inbox.InboxError, match="ไม่พบรหัส SB-XX001"):
        inbox_hr.apply(FID, "p16:warning:SB-PD007", {"employee_code": "SB-XX001"})


# ------------------------------------------------------------------ the items ride along with the preview
def test_items_survive_a_choice_a_reread_and_saved_fixes(db, read, monkeypatch):
    inbox_hr.apply(FID, "p14:loan:SB-FR024", {})
    inbox_hr.dismiss(FID, "p18:warning:SB-FR076")
    want = items_of(db)
    again = inbox_ai.apply_choices(json.loads(json.dumps(db.t["inbox_files"][0]["preview"])), {"month": "2026-11"})
    assert {it["id"]: it for it in again["hr_items"]} == want
    from tests.test_inbox import fake_reader
    monkeypatch.setattr(inbox, "_readers", lambda: [fake_reader("scan", 0)])
    out = inbox.save_edits(FID, {"edits": {"p2:l1": {"amount": 450}}}, True, None, "ok")
    assert items_of(db) == want and out["hr"]["items"][0]["status"] in ("applied", "pending", "dismissed")
    inbox.reread(FID)
    assert items_of(db) == want


def test_a_fresh_paid_read_makes_new_items(db, monkeypatch):
    ai_on()
    c = HrClient(ANSWER[:2], pages=[{k: v for k, v in r.items() if k != "n"} for r in reads()])
    monkeypatch.setattr(inbox_ai, "_client", lambda: c)
    monkeypatch.setattr(inbox_ai, "page_images", lambda name, data: [b"jpg"] * 2)
    monkeypatch.setattr(inbox_ai, "is_expense_document", lambda r: True)
    monkeypatch.setattr(inbox_ai, "from_pages", lambda reads_, name, choices, usage=None, images=None, line_edits=None:
                        {**json.loads(json.dumps(PV_CACHE[0])), "ai_usage": usage, "flags": []})
    from app import acc_docs
    monkeypatch.setattr(acc_docs, "store_page_images", lambda prefix, imgs: {})
    pv = inbox_ai.run({"file_name": "เอกสาร 341.pdf"}, b"%PDF-")
    assert [it["id"] for it in pv["hr_items"]] == ["p14:loan:SB-FR024", "p15:role_change:SB-PD011"]
    assert c.hr_calls == 1 and pv["ai_usage"]["calls"] == 3 and pv["ai_usage"]["input_tokens"] == 900      # 2 pages + the HR call


PV_CACHE: list = []


@pytest.fixture(autouse=True)
def _cache_pv(pv):
    PV_CACHE[:] = [pv]


def test_a_failed_hr_call_never_fails_the_read(monkeypatch, pv):
    class Broken:
        beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("timeout"))))
    monkeypatch.setattr(inbox_ai, "_client", lambda: Broken())
    p = json.loads(json.dumps(pv))
    usage = {"model": "m", "calls": 2, "input_tokens": 10, "output_tokens": 5}
    inbox_ai._read_hr(p, usage)
    assert "hr_items" not in p and usage["calls"] == 2 and "อ่านเรื่องพนักงาน" in p["flags"][-1]


def test_on_demand_read_for_a_file_read_before_and_its_usage(db, pv, client_hr):
    ai_on()
    file_row(db, pv)                                                       # read before this page: no hr_items
    assert inbox.get(FID, True, "ok")["hr"]["can_read"] is True
    inbox_hr.read_file(FID)
    row = db.t["inbox_files"][0]
    assert len(row["preview"]["hr_items"]) == 8 and row["ai_usage"]["calls"] == 22 and row["preview"]["ai_usage"]["calls"] == 22
    inbox_hr.apply(FID, "p14:loan:SB-FR024", {})
    inbox_hr.read_file(FID)                                                # again: what was recorded stays recorded
    assert items_of(db)["p14:loan:SB-FR024"]["status"] == "applied" and len(items_of(db)) == 8


def test_the_same_bytes_read_again_keep_what_was_recorded(monkeypatch, pv):
    import hashlib
    from tests.test_inbox_ai import pdf, use_db
    data = pdf(2)
    items = [{"id": "a", "status": "applied", "result": "x"}, {"id": "b", "status": "dismissed", "result": "ไม่บันทึก"}, {"id": "c", "status": "pending"}]
    use_db(monkeypatch, FakeDB(vehicles=VEH, inbox_files=[{"sha256": hashlib.sha256(data).hexdigest(), "preview": {**pv, "hr_items": items}}]))
    monkeypatch.setattr(inbox_ai, "_earlier_docs", lambda *a: [])
    got = inbox_r_scan.read(ic.sniff_file("x.pdf", data, {}))
    assert [(it["id"], it["status"]) for it in got["hr_items"]] == [("a", "applied"), ("b", "pending"), ("c", "pending")]


# ------------------------------------------------------------------ review fixes (28 Nov 2026)
def test_a_dismiss_here_never_overwrites_what_the_queue_recorded(db, read):
    """พนักงาน › จากเอกสารสิ้นเดือน already applied p17 (same batch key): ไม่บันทึก here and เอากลับมา leave that row alone."""
    db.t["hr_suggestions"].append({"id": "s1", "batch": "docs:2026-11:สระบุรี:end", "page": 17, "kind": "warning", "employee_code": "SB-FR034",
                                   "payload": {}, "status": "applied", "result": "เพิ่มหนังสือเตือนครั้งที่ 2 (เซ็นแล้ว)"})
    inbox_hr.dismiss(FID, "p17:warning:SB-FR034")
    inbox_hr.reopen(FID, "p17:warning:SB-FR034")
    assert [(h["id"], h["status"], h["result"]) for h in db.t["hr_suggestions"]] == [("s1", "applied", "เพิ่มหนังสือเตือนครั้งที่ 2 (เซ็นแล้ว)")]
    inbox_hr.apply(FID, "p17:warning:SB-FR034", {})                        # nor does a บันทึก here
    assert [(h["id"], h["status"]) for h in db.t["hr_suggestions"]] == [("s1", "applied")]


def test_a_dismiss_here_leaves_a_waiting_queue_row_and_an_applied_one_here_replaces_it(db, read):
    db.t["hr_suggestions"].append({"id": "s2", "batch": "docs:2026-11:สระบุรี:end", "page": 14, "kind": "loan", "employee_code": "SB-FR024",
                                   "payload": {}, "status": "pending", "result": None})
    inbox_hr.dismiss(FID, "p14:loan:SB-FR024")
    inbox_hr.reopen(FID, "p14:loan:SB-FR024")
    assert [(h["id"], h["status"]) for h in db.t["hr_suggestions"]] == [("s2", "pending")]
    inbox_hr.apply(FID, "p14:loan:SB-FR024", {})
    assert [(h["id"], h["status"], h["result"].endswith(inbox_hr.HERE)) for h in db.t["hr_suggestions"]] == [("s2", "applied", True)]


def test_a_recorded_warning_keeps_the_level_it_was_written_with(db, read):
    inbox_hr.apply(FID, "p17:warning:SB-FR034", {"fields": {"level": None}})   # left on "ตามระบบ"
    assert items_of(db)["p17:warning:SB-FR034"]["fields"]["level"] == 2
    assert hr_view()["p17:warning:SB-FR034"]["dest"] == "พนักงาน · นายลามิน (SB-FR034) · หนังสือเตือนครั้งที่ 2"


def test_a_last_day_before_the_first_day_is_refused(db, read):
    next(p for p in db.t["employees"] if p["code"] == "SB-FR101")["start_date"] = "2026-08-01"
    assert hr_view()["p21:resign:SB-FR101"]["block"] == "วันสุดท้ายต้องไม่ก่อนวันเริ่มงาน (01/08/2569)"
    with pytest.raises(inbox.InboxError, match="วันสุดท้ายต้องไม่ก่อนวันเริ่มงาน"):
        inbox_hr.apply(FID, "p21:resign:SB-FR101", {})
    from app import hr_suggest
    emp = next(p for p in db.t["employees"] if p["code"] == "SB-FR101")
    with pytest.raises(employees.InvalidEmployee, match="วันสุดท้ายต้องไม่ก่อนวันเริ่มงาน"):
        hr_suggest.write_resign(emp, "2026-06-02", None)
    assert emp["end_date"] == "2026-11-02"


def test_a_code_with_a_fourth_digit_is_not_a_shorter_one():
    more = PEOPLE + [{"id": uid(10), "code": "SB-FR1010", "prefix": "นาย", "full_name": "สมชาย", "nickname": "", "branch": "สระบุรี", "status": "active"}]
    assert inbox_hr.norm_code("SB-FR1010") is None and inbox_hr.norm_code("SB-FR101") == "SB-FR101"
    assert inbox_hr.resolve("SB-FR1010", "", more)[0]["code"] == "SB-FR1010"
    assert inbox_hr.resolve("SB-FR0761", "", more) == (None, None)


def test_a_new_read_never_hides_another_person_behind_an_old_decision():
    old = [{"id": "p20:resign:x", "page": 20, "kind": "resign", "line_key": None, "employee_name_read": "A", "employee_code": "SB-FR024",
            "status": "applied"}]
    new = [{"id": "p20:resign:x", "page": 20, "kind": "resign", "line_key": None, "employee_name_read": "B", "employee_code": None, "status": "pending"},
           {"id": "p20:resign:x:2", "page": 20, "kind": "resign", "line_key": None, "employee_name_read": "A", "employee_code": None, "status": "pending"}]
    got = inbox_hr.merge(old, new)
    assert [(it["employee_name_read"], it["status"]) for it in got] == [("B", "pending"), ("A", "applied")]
    assert len({it["id"] for it in got}) == 2 and got[1]["id"] == "p20:resign:x"
    # a stable id (the person's code) still matches when the name is written another way
    old = [{"id": "p17:warning:SB-FR034", "page": 17, "kind": "warning", "employee_name_read": "ลามิน", "employee_code": "SB-FR034", "status": "applied"}]
    new = [{"id": "p17:warning:SB-FR034", "page": 17, "kind": "warning", "employee_name_read": "นายลามิน", "employee_code": "SB-FR034", "status": "pending"}]
    assert inbox_hr.merge(old, new) == old


def test_tokens_spent_are_counted_when_making_the_items_fails(db, pv, client_hr, monkeypatch):
    monkeypatch.setattr(inbox_hr, "build_items", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    with pytest.raises(inbox_hr.HrReadError) as exc:
        inbox_hr.read_items(pv)
    assert exc.value.usage["calls"] == 1
    p = json.loads(json.dumps(pv))
    usage = {"model": "m", "calls": 2, "input_tokens": 10, "output_tokens": 5}
    inbox_ai._read_hr(p, usage)
    assert usage["calls"] == 3 and "hr_items" not in p


def test_the_people_are_read_before_the_paid_call(db, pv, client_hr, monkeypatch):
    monkeypatch.setattr(inbox_hr, "_people", lambda: (_ for _ in ()).throw(supabase.SupabaseError("down")))
    with pytest.raises(supabase.SupabaseError):
        inbox_hr.read_items(pv)
    assert client_hr.hr_calls == 0


# ------------------------------------------------------------------ the routes and who may use them
def _client(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    return TestClient(main.app)


def test_routes_and_who_may_record(db, read, monkeypatch):
    people = {"u_books": {"id": 1, "name": "คนทำบัญชี", "role": "staff", "menus": ["accounts_in"]},
              "u_pnl": {"id": 2, "name": "ดูงบ", "role": "staff", "menus": ["accounts"]},
              "u_hr": {"id": 3, "name": "ฝ่ายบุคคล", "role": "staff", "menus": ["accounts_in", "employees"]}}
    monkeypatch.setattr(users, "session_user", lambda t: people.get(t))
    c = _client(monkeypatch)
    url = f"/api/accounts/inbox/{FID}/hr/p14:loan:SB-FR024"
    assert c.post(f"{url}/apply", headers={"X-App-Token": "u_pnl"}, json={}).status_code == 403          # P&L only: not even the drop box
    r = c.post(f"{url}/apply", headers={"X-App-Token": "u_books"}, json={})
    assert r.status_code == 403 and "เมนูรายชื่อพนักงาน" in r.json()["detail"]                           # the drop box, but no พนักงาน
    one = c.get(f"/api/accounts/inbox/{FID}", headers={"X-App-Token": "u_books"}).json()["hr"]
    assert one["access"] == "none" and all(it["now"] is None for it in one["items"])
    assert len([ln for ln in db.t["employee_loans"] if ln["employee_id"] == uid(1)]) == 0
    object.__setattr__(settings, "hr_pin", "1234")
    try:
        assert c.post(f"{url}/apply", headers={"X-App-Token": "u_hr"}, json={}).status_code == 423
        assert c.get(f"/api/accounts/inbox/{FID}", headers={"X-App-Token": "u_hr"}).json()["hr"]["access"] == "locked"
        r = c.post(f"{url}/apply", headers={"X-App-Token": "u_hr", "X-HR-Pin": "1234"}, json={"fields": {"repay_amount": 500}})
        assert r.status_code == 200, r.text
        got = {it["id"]: it for it in r.json()["hr"]["items"]}
        assert got["p14:loan:SB-FR024"]["status"] == "applied" and got["p14:loan:SB-FR024"]["applied_by"] == "ฝ่ายบุคคล"
        again = c.post(f"{url}/apply", headers={"X-App-Token": "secret", "X-HR-Pin": "1234"}, json={})
        assert again.status_code == 422 and "บันทึกซ้ำไม่ได้" in again.json()["detail"]
        h = {"X-App-Token": "secret", "X-HR-Pin": "1234"}
        assert c.post(f"/api/accounts/inbox/{FID}/hr/p2:l2:loan/dismiss", headers=h).status_code == 200
        r = c.post(f"/api/accounts/inbox/{FID}/hr/p2:l2:loan/reopen", headers=h)
        assert r.status_code == 200 and {it["id"]: it for it in r.json()["hr"]["items"]}["p2:l2:loan"]["status"] == "pending"
        assert c.post(f"/api/accounts/inbox/{FID}/hr/nope/apply", headers=h, json={}).status_code == 404
        r = c.post(f"/api/accounts/inbox/{FID}/hr/p18:warning:SB-FR076/person", headers=h, json={"employee_code": "SB-FR034"})
        assert r.status_code == 200 and {it["id"]: it for it in r.json()["hr"]["items"]}["p18:warning:SB-FR076"]["employee"]["code"] == "SB-FR034"
    finally:
        object.__setattr__(settings, "hr_pin", "")
    assert [ln["repay_amount"] for ln in db.t["employee_loans"] if ln["employee_id"] == uid(1)] == [500.0]


def test_the_hr_paths_belong_to_the_inputs_menu_only():
    for path in (f"/api/accounts/inbox/{FID}/hr/read", f"/api/accounts/inbox/{FID}/hr/p14:loan:SB-FR024/apply"):
        assert users.allowed(["accounts_in"], path) and not users.allowed(["accounts"], path)


def test_the_history_list_knows_role_changes():
    assert hr_suggest.KINDS["role_change"] == "เปลี่ยนตำแหน่ง"
    assert "role_change" in open("schema_v95.sql", encoding="utf-8").read()


# ------------------------------------------------------------------ the page's own helpers (static/inboxcheck.js, in Node)
def test_the_page_puts_each_item_where_it_belongs(read, tmp_path):
    from tests.test_inbox_check_page import NODE, run
    if NODE is None:
        pytest.skip("Node is not installed")
    f = inbox.get(FID, True, "ok")
    got = run("""(() => {
      const f = DATA.f, pages = ickPages(f);
      const split = (n) => { const s = ickHrSplit(f, pages.find((p) => p.n === n)); return [s.byLine.map((i) => i.id), s.onPage.map((i) => i.id)]; };
      ickHr.drafts["p14:loan:SB-FR024"] = { first_month: "2027-01" };
      return { p2: split(2), p17: split(17), pending16: ickHrPendingOn(f, 16), when: ickHrWhen("2026-11-28T07:05:00+00:00"),
               month: ickHrValue(f.hr.items.find((i) => i.id === "p14:loan:SB-FR024"), "first_month"),
               read: ickHrValue(f.hr.items.find((i) => i.id === "p17:warning:SB-FR034"), "penalty_amount") };
    })()""", {"f": json.loads(json.dumps(f, default=str))}, tmp_path)
    assert got["p2"] == [["p2:l2:loan"], []] and got["p17"] == [[], ["p17:warning:SB-FR034"]]
    assert got["pending16"] == 1 and got["when"] == "28/11/2569 14:05" and got["month"] == "2027-01" and got["read"] == "1000"
