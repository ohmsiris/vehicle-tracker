import base64
import uuid
from datetime import date

import pytest

from app import documents as dc
from app import employees as em

TODAY = date(2026, 10, 26)
E1 = "11111111-1111-1111-1111-111111111111"
E2 = "22222222-2222-2222-2222-222222222222"
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAjyA1VQAAAABJRU5ErkJggg=="


def warning(**kw):
    return {"doc_type": "warning", "employee_id": E1, "doc_date": "2026-10-20",
            "content": {"offence_date": "1/10/2569", "offence": "หยุดงานโดยไม่แจ้ง", "punishment": "หักเงิน 1,000 บาท", "penalty_amount": "1,000"}, **kw}


def memo(**kw):
    return {"doc_type": "memo", "employee_id": E1, "subject": "ขออนุมัติหักเงิน", "content": {"body": "ตามที่ …"}, **kw}


# ------------------------------------------------------------------ pure
def test_thai_dates_and_numbers():
    assert dc.thai_date("2026-10-26") == "26 ตุลาคม 2569" and dc.thai_date("1/10/2569") == "1/10/2569" and dc.thai_date(None) == ""
    assert dc.number_for("สระบุรี", 2569, 1) == "SB001/2569" and dc.number_for("แก่งคอย", 2569, 12) == "KK012/2569" and dc.number_for("สระบุรี", 2570, 123) == "SB123/2570"
    assert dc.next_seq([]) == 1 and dc.next_seq([1, 2, 5]) == 6


def test_a_new_warning_gets_the_standard_subject_and_keeps_its_fields():
    v = dc.clean_document(warning(), today=TODAY)
    assert v["doc_type"] == "warning" and v["subject"] == dc.WARNING_SUBJECT and v["with_signature"] is True and v["warning_level"] == 1
    assert v["content"] == {"offence_date": "1/10/2569", "offence": "หยุดงานโดยไม่แจ้ง", "punishment": "หักเงิน 1,000 บาท", "penalty_amount": 1000.0, "show_witnesses": False}


def test_a_draft_may_be_half_written_and_a_memo_defaults_to_the_manager():
    v = dc.clean_document({"doc_type": "warning", "employee_id": E1}, today=TODAY)
    assert v["content"]["offence"] == "" and v["doc_date"] == "2026-10-26"
    m = dc.clean_document(memo(), today=TODAY)
    assert m["content"]["to"] == dc.MEMO_TO and m["content"]["body"] == "ตามที่ …" and "warning_level" not in m


@pytest.mark.parametrize("data,frag", [
    ({"doc_type": "x", "employee_id": E1}, "ประเภทเอกสาร"), ({"doc_type": "memo", "employee_id": "no"}, "เลือกพนักงาน"), (warning(doc_date="2026-12-01"), "อนาคต"),
    (warning(doc_date="2569-01-01"), "ค.ศ."), (warning(doc_date="xx"), "ไม่ถูกต้อง"), (warning(warning_level="9"), "ระดับ"),
    (warning(content={"offence": "x", "punishment": "y", "penalty_amount": "abc"}), "ยอดที่หัก"), (warning(loan_id="nope"), "เงินกู้"),
])
def test_bad_documents_are_refused_in_thai(data, frag):
    with pytest.raises(dc.InvalidDocument, match=frag):
        dc.clean_document(data, today=TODAY)


def test_the_level_to_suggest_follows_the_written_warnings_that_still_count():
    w = lambda level, status="issued": {"doc_type": "warning", "warning_level": level, "status": status}
    assert dc.next_level([]) == 1
    assert dc.next_level([w(1)]) == 2 and dc.next_level([w(1), w(2, "signed")]) == 3 and dc.next_level([w(3)]) == 3
    assert dc.next_level([w(2, "cancelled"), w(1)]) == 2 and dc.next_level([w(0)]) == 1 and dc.next_level([w(1, "draft")]) == 1
    assert dc.next_level([{"doc_type": "memo", "warning_level": None, "status": "issued"}]) == 1


def test_the_signature_must_be_a_small_real_png():
    assert dc.valid_signature(PNG) == PNG
    for bad, frag in (("data:image/jpeg;base64,AAAA", "PNG"), ("hello", "PNG"), ("data:image/png;base64,AAAA", "ไม่ใช่ภาพ PNG"),
                      ("data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 500_000).decode(), "ใหญ่เกินไป")):
        with pytest.raises(dc.InvalidDocument, match=frag):
            dc.valid_signature(bad)


# ------------------------------------------------------------------ the database calls, on a small fake
class Fake:
    def __init__(self):
        self.t = {"employees": [{"id": E1, "code": "SB-FR001", "prefix": "นาย", "gender": "ชาย", "full_name": "จรูญ มินฉิมพลี", "position": "ช่าง", "branch": "สระบุรี", "status": "active"},
                                {"id": E2, "code": "SB-KK003", "prefix": "นาย", "gender": "ชาย", "full_name": "สมชาย เพชรจันทร์", "position": "พนักงานขับรถหน้าโรง", "branch": "แก่งคอย", "status": "active"}],
                  "employee_documents": [], "employee_loans": [], "hr_settings": [], "employee_rates": [], "attendance_punches": [], "attendance_reviews": []}

    def _eq(self, rows, params):
        eq = {k: v[3:] for k, v in (params or {}).items() if isinstance(v, str) and v.startswith("eq.")}
        return [r for r in rows if all(str(r.get(k)) == v for k, v in eq.items())]

    def select(self, table, params=None):
        return [dict(r) for r in self._eq(self.t[table], params)]

    select_all = select

    def insert(self, table, rows, on_conflict=None, **kw):
        out = []
        for r in rows:
            if on_conflict:
                for old in [o for o in self.t[table] if o.get(on_conflict) == r.get(on_conflict)]:
                    self.t[table].remove(old)
            row = {"id": str(uuid.uuid4()), "status": "draft", "with_signature": True, **r}
            self.t[table].append(row)
            out.append(dict(row))
        return out

    def update(self, table, match, values):
        rows = self._eq(self.t[table], match)
        for r in rows:
            r.update(values)
        return rows

    def delete(self, table, match):
        for r in self._eq(self.t[table], match):
            self.t[table].remove(r)


@pytest.fixture
def db(monkeypatch):
    d = Fake()
    for mod in (dc, em):
        for name in ("select", "select_all", "insert", "update", "delete"):
            monkeypatch.setattr(mod.supabase, name, getattr(d, name))
    monkeypatch.setattr(dc, "has_v24", lambda: True)
    monkeypatch.setattr(dc.loans, "has_v23", lambda: True)
    monkeypatch.setattr(em, "has_v21", lambda: True)
    monkeypatch.setattr(em, "today_th", lambda: TODAY)
    monkeypatch.setattr(dc.employees, "today_th", lambda: TODAY)
    return d


def test_issuing_numbers_per_branch_and_year_and_freezes_the_paper(db):
    a = dc.create_document(warning())
    assert a["status"] == "draft" and a["number"] == "" and a["warning_level"] == 1 and a["render"]["employee"]["display_name"] == "นายจรูญ มินฉิมพลี" and a["frozen"] is False
    first = dc.issue_document(a["id"])
    assert first["status"] == "issued" and first["number"] == "SB001/2569" and first["frozen"] is True
    second = dc.issue_document(dc.create_document(warning())["id"])
    assert second["number"] == "SB002/2569"
    kk = dc.issue_document(dc.create_document(warning(employee_id=E2))["id"])
    assert kk["number"] == "KK001/2569" and kk["render"]["letterhead"]["address"].startswith("เลขที่ 150")
    new_year = dc.issue_document(dc.create_document(warning(doc_date="2026-01-05"))["id"])
    assert new_year["number"] == "SB003/2569"                                  # the same พ.ศ.: the running number continues
    with pytest.raises(dc.InvalidDocument, match="ออกเอกสารไปแล้ว"):
        dc.issue_document(a["id"])


def test_an_issued_paper_does_not_change_when_the_employee_record_does(db):
    doc = dc.issue_document(dc.create_document(warning())["id"])
    db.t["employees"][0].update(full_name="ชื่อใหม่", position="หัวหน้างาน")
    again = dc.get_document(doc["id"])
    assert again["render"]["employee"]["display_name"] == "นายจรูญ มินฉิมพลี" and again["render"]["employee"]["position"] == "ช่าง"
    draft = dc.create_document(warning())
    assert draft["render"]["employee"]["display_name"] == "นายชื่อใหม่" and draft["render"]["employee"]["position"] == "หัวหน้างาน"      # a draft follows the record


def test_only_a_draft_can_be_edited_or_deleted_and_an_issued_one_is_cancelled(db):
    d = dc.create_document(warning())
    edited = dc.update_document(d["id"], {"subject": "เรื่องใหม่", "content": {**d["content"], "offence": "มาสาย"}})
    assert edited["subject"] == "เรื่องใหม่" and edited["content"]["offence"] == "มาสาย"
    issued = dc.issue_document(d["id"])
    with pytest.raises(dc.InvalidDocument, match="แก้ไม่ได้"):
        dc.update_document(d["id"], {"subject": "x"})
    with pytest.raises(dc.InvalidDocument, match="ลบได้เฉพาะร่าง"):
        dc.delete_document(d["id"])
    assert dc.cancel_document(d["id"], {"note": "พิมพ์ผิด"})["status"] == "cancelled" and issued["number"]
    with pytest.raises(dc.InvalidDocument):
        dc.cancel_document(d["id"], {})
    draft = dc.create_document(memo())
    dc.delete_document(draft["id"])
    assert all(x["id"] != draft["id"] for x in db.t["employee_documents"])


def test_an_incomplete_draft_cannot_be_issued(db):
    with pytest.raises(dc.InvalidDocument, match="ความผิด"):
        dc.issue_document(dc.create_document({"doc_type": "warning", "employee_id": E1})["id"])
    with pytest.raises(dc.InvalidDocument, match="การลงโทษ"):
        dc.issue_document(dc.create_document(warning(content={"offence": "x"}))["id"])
    with pytest.raises(dc.InvalidDocument, match="เนื้อหา"):
        dc.issue_document(dc.create_document({"doc_type": "memo", "employee_id": E1, "subject": "x"})["id"])


def test_the_signature_is_a_print_choice_that_stays_open_after_issuing(db):
    d = dc.issue_document(dc.create_document(warning())["id"])
    assert d["with_signature"] is True
    assert dc.set_print_options(d["id"], {"with_signature": False, "show_witnesses": True})["with_signature"] is False
    assert dc.get_document(d["id"])["content"]["show_witnesses"] is True and dc.get_document(d["id"])["content"]["offence"] == "หยุดงานโดยไม่แจ้ง"


def test_the_employee_signing_or_refusing(db):
    d = dc.create_document(warning())
    with pytest.raises(dc.InvalidDocument, match="ออกเอกสารก่อน"):
        dc.acknowledge(d["id"], {"kind": "signed"})
    dc.issue_document(d["id"])
    s = dc.acknowledge(d["id"], {"kind": "signed", "date": "2026-10-21"})
    assert s["status"] == "signed" and s["acknowledged_date"] == "2026-10-21"
    with pytest.raises(dc.InvalidDocument, match="พยาน"):
        dc.acknowledge(d["id"], {"kind": "refused", "date": "2026-10-21"})
    r = dc.acknowledge(d["id"], {"kind": "refused", "date": "2026-10-21", "witnesses": "นายก, นายข"})
    assert r["status"] == "refused" and r["witnesses"] == "นายก, นายข"
    assert dc.acknowledge(d["id"], {"kind": "issued"})["status"] == "issued"
    for bad in ({"kind": "signed", "date": "2026-12-01"}, {"kind": "signed", "date": "2026-10-01"}, {"kind": "x"}):
        with pytest.raises(dc.InvalidDocument):
            dc.acknowledge(d["id"], bad)


def test_the_next_warning_level_is_suggested_from_the_history(db):
    assert dc.suggest(E1)["next_level"] == 1
    dc.issue_document(dc.create_document(warning())["id"])
    assert dc.suggest(E1)["next_level"] == 2 and dc.suggest(E1)["warnings"][0]["number"] == "SB001/2569"
    second = dc.create_document(warning())                       # no level chosen: the suggestion is used
    assert second["warning_level"] == 2
    verbal = dc.create_document(warning(warning_level=0))        # a verbal warning (0) is a choice, not "empty"
    assert verbal["warning_level"] == 0


def test_a_loan_can_be_attached_to_a_memo_only_if_it_is_the_employees_own(db):
    db.t["employee_loans"].append({"id": "33333333-3333-3333-3333-333333333333", "employee_id": E2, "kind": "accident", "amount": 5500, "loan_date": "2026-10-20",
                                   "repay_mode": "monthly_fixed", "repay_amount": 2000, "first_month": "2026-11-01", "status": "active"})
    with pytest.raises(dc.InvalidDocument, match="ไม่ใช่ของพนักงานคนนี้"):
        dc.create_document(memo(loan_id="33333333-3333-3333-3333-333333333333"))
    d = dc.issue_document(dc.create_document(memo(employee_id=E2, loan_id="33333333-3333-3333-3333-333333333333"))["id"])
    assert d["render"]["loan"]["amount"] == 5500.0 and [r["amount"] for r in d["render"]["loan"]["schedule"]] == [2000.0, 2000.0, 1500.0]


def test_a_resigned_employee_gets_no_new_papers_and_one_with_papers_cannot_be_deleted(db, monkeypatch):
    d = dc.create_document(warning())
    monkeypatch.setattr(em, "REFERENCED_BY", [("employee_documents", "employee_id")])
    with pytest.raises(em.InvalidEmployee, match="ลาออก"):
        em.delete_employee(E1)
    db.t["employees"][0]["status"] = "resigned"
    with pytest.raises(dc.InvalidDocument, match="ลาออกแล้ว"):
        dc.create_document(warning())
    with pytest.raises(dc.InvalidDocument, match="ลาออกแล้ว"):
        dc.duplicate_document(d["id"])


def test_duplicating_makes_a_fresh_draft_with_the_same_words(db):
    old = dc.issue_document(dc.create_document(warning())["id"])
    copy = dc.duplicate_document(old["id"])
    assert copy["status"] == "draft" and copy["number"] == "" and copy["content"]["offence"] == old["content"]["offence"] and copy["id"] != old["id"]


def test_the_signature_is_stored_apart_from_the_papers(db):
    assert dc.get_signature() is None
    assert dc.set_signature(PNG) == {"has_signature": True} and dc.get_signature() == PNG
    dc.set_signature(PNG)
    assert len(db.t["hr_settings"]) == 1
    assert dc.list_documents()["has_signature"] is True and all("render" not in x for x in dc.list_documents()["documents"])
    assert dc.clear_signature() == {"has_signature": False} and dc.get_signature() is None
