"""The drop box's PEA reader (app/inbox_r_pea.py): removing a bill re-books the cheque run that paid it, and an AMR report can be
saved although it books no ledger rows. Supabase is faked throughout (nothing here touches the live database)."""
import os

import pytest

from app import amr, electricity, inbox_common as ic
from app import inbox_r_pea as rp
from app import pea_bill, supabase


def _fixture(name):
    return open(os.path.join(os.path.dirname(__file__), "fixtures", name), encoding="utf-8").read()


@pytest.fixture
def no_db(monkeypatch):
    """Every supabase call is recorded; none reaches the network."""
    calls = []
    monkeypatch.setattr(supabase, "select", lambda table, params: calls.append(("select", table)) or [{"branch": "แก่งคอย"}])
    monkeypatch.setattr(supabase, "select_all", lambda table, params, page=1000: calls.append(("select_all", table)) or [])
    monkeypatch.setattr(supabase, "delete", lambda table, match: calls.append(("delete", table, match)))
    monkeypatch.setattr(supabase, "insert", lambda table, rows, **kw: calls.append(("insert", table)) or rows)
    monkeypatch.setattr(supabase, "update", lambda table, match, values: calls.append(("update", table)) or [])
    return calls


def _bill(monkeypatch, text):
    monkeypatch.setattr(pea_bill, "parse_pdf", lambda data: pea_bill.parse_text(text))
    return rp.read(ic.Dropped(name="Feb.pdf", data=b"%PDF-", ext=".pdf", text=text, pages=1))


# ------------------------------------------------------------------ removing a bill
def test_unbook_of_a_bill_asks_to_rebook_the_next_cheque_run(monkeypatch, no_db):
    """Removing a booked bill must turn the cheque run that paid it (Dr 2100 'pays_bill') back into the month's electricity cost:
    unbook returns the same rebook request book() makes (contract 1) and the engine runs it."""
    p = _bill(monkeypatch, _fixture("pea_kk_2026_02.txt"))
    out = rp.unbook(p)
    assert out == {"rebook": [{"kind": "cheques", "month": "2026-03"}]}
    assert ("delete", "elec_bills", {"invoice_no": "eq.312510999861"}) in no_db
    assert ("delete", "acc_entries", {"source_id": "eq.pea:312510999861"}) in no_db
    # booking asks for exactly the same cheque run, so remove and book are symmetrical
    monkeypatch.setattr(electricity, "save_bill", lambda bill, branch, file_id=None: None)
    assert rp.book(p, "f")["rebook"] == out["rebook"]


def test_unbook_of_two_bills_of_one_month_asks_once_and_handles_december(monkeypatch, no_db):
    b = {"key": "pea:1", "month": "2026-12", "bill": {"invoice_no": "1", "bill_period": "2026-12"}}
    c = {"key": "pea:2", "month": "2026-12", "bill": {"invoice_no": "2", "bill_period": "2026-12"}}
    d = {"key": "pea:3", "month": "2026-11", "bill": {}}                       # an old preview without the bill: key + batch month
    out = rp.unbook({"batches": [b, c, d]})
    assert out["rebook"] == [{"kind": "cheques", "month": "2027-01"}, {"kind": "cheques", "month": "2026-12"}]
    assert ("delete", "elec_bills", {"invoice_no": "eq.3"}) in no_db


def test_unbook_of_an_amr_report_touches_nothing(no_db):
    assert rp.unbook({"batches": [], "amr": {"ca_no": "x"}, "books_without_batches": True}) == {"rebook": []}
    assert no_db == []


# ------------------------------------------------------------------ the AMR report
AMR = """<td colspan='5' class='header'>[ประจำเดือน : ตุลาคม 2569]</td></tr>
<tr><td class='detail'>บัญชีผู้ใช้ไฟ : </td><td>020019229331&nbsp;</td><td class='detail'>ชื่อผู้ใช้ไฟ : </td><td>x</td><td></td></tr>
<tr><td class='detail'>หมายเลขมิเตอร์ : </td><td>27666994&nbsp;</td></tr> รายงานข้อมูลกิโลวัตต์ชั่วโมงรายเดือน AMR
<tr><td class='leftcenter'>&nbsp;01/10/2026 00.15</td><td class='rightdetail'></td><td class='rightdetail'>106.43</td><td class='rightdetail'></td><td class='rightdetail'>106.43</td></tr>
<tr><td class='leftcenter'>&nbsp;01/10/2026 09.15</td><td class='rightdetail'>80</td><td class='rightdetail'></td><td class='rightdetail'></td><td class='rightdetail'>80</td></tr>
"""


def test_amr_preview_declares_it_books_without_batches_and_book_saves_the_readings(monkeypatch, no_db):
    """Contract 2: an AMR report has no ledger batches; the preview says so, and ลงบัญชี still saves the 15-minute readings."""
    p = rp.read(ic.sniff_file("kWh02001922933103112026215412.xls", AMR.encode("utf-8")))
    assert p["books_without_batches"] is True and p["batches"] == [] and not p["store_only"] and not p["conflicts"]
    got = []
    monkeypatch.setattr(amr, "save", lambda rep, branch: got.append((rep["ca_no"], rep["month"], len(rep["rows"]), branch)))
    assert rp.book(p, "f") == {"batches": [], "booked": 0.0, "rebook": []}
    assert got == [("020019229331", "2026-10", 2, "แก่งคอย")]


def test_a_pea_bill_and_an_empty_amr_do_not_claim_to_book_without_batches(monkeypatch, no_db):
    assert not _bill(monkeypatch, _fixture("pea_kk_2026_02.txt")).get("books_without_batches")
    empty = rp.read(ic.sniff_file("kWh1.xls", "รายงานข้อมูลกิโลวัตต์ชั่วโมงรายเดือน AMR [ประจำเดือน : ]".encode("utf-8")))
    assert empty["store_only"] and not empty.get("books_without_batches")


def test_amr_of_a_new_meter_asks_the_branch_first(monkeypatch, no_db):
    monkeypatch.setattr(supabase, "select", lambda table, params: [])
    p = rp.read(ic.sniff_file("kWh1.xls", AMR.encode("utf-8")))
    assert [c["key"] for c in p["conflicts"]] == ["branch"] and p["books_without_batches"]
    with pytest.raises(ValueError):
        rp.book(p, "f")                                                       # the engine refuses first (open conflict); never a guess
    f = ic.sniff_file("kWh1.xls", AMR.encode("utf-8"), {"branch": "สระบุรี"})
    assert rp.read(f)["amr"]["branch"] == "สระบุรี"
