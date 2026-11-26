"""The drop box readers for HQ's cheque summary (app/inbox_r_cheques.py) and PEA files (app/inbox_r_pea.py), and what they changed in
app/acc_cheques.py. No test touches the database: app.supabase is faked wherever a reader would read or write it."""
import io
import os
from datetime import date

import pytest

from app import acc_cheques as ac
from app import inbox_common as ic
from app import inbox_r_cheques as rc
from app import inbox_r_pea as rp
from app import pea_bill, supabase


# ------------------------------------------------------------------ acc_cheques fixes
def test_junk_in_the_date_column_is_never_a_date():
    assert ac.be_date(185000.0) is None              # an amount typed in the date cell -> 1863
    assert ac.be_date(41653.0) is None               # an Excel serial of 2014
    assert ac.be_date("26/052569") is None
    assert ac.be_date("31/02/2569") is None
    assert ac.be_date(244592.0) == date(2026, 8, 31)
    assert ac.be_date("08/05/69") == date(2026, 5, 8)
    assert ac.be_date("20/1/2569") == date(2026, 1, 20)


def _row(what, payee="", amount=1000.0, note="", no=1, cheque_date=None):
    return {"no": no, "what": what, "payee": payee, "amount": amount, "cheque_date": cheque_date, "cheque_no": "1", "bank": "", "note": note}


def test_a_pea_cheque_within_one_baht_pays_the_bill_without_a_flag():
    bills = {835380.28: ("318011438503", "2026-10")}
    out = ac.build([_row("ค่าไฟฟ้า 72/9เดือน10/69", "ซื้อแคชเชียร์เช็ค", 835380.25, cheque_date=date(2026, 11, 20))],
                   branch="สระบุรี", file_month=date(2026, 11, 1), batch="t", bills=bills)
    e = out["entries"][0]
    assert e["kind"] == "pays_bill" and e["entry_date"] == "2026-11-20"
    assert {ln["account"]: ln["debit"] for ln in e["lines"] if ln["debit"]} == {"2100": 835380.25}
    assert not out["flags"]
    assert out["matched"] == [{"row": "r1", "pea": "318011438503", "period": "2026-10"}]


PURCHASES = {"PC6906-081": {"bill_no": "PC6906-081", "total": 45582.0, "branch": "สระบุรี", "purpose": "stock"},
             "PC6906-08": {"bill_no": "PC6906-08", "total": 999.0, "branch": "สระบุรี", "purpose": "stock"},
             "REF12345": {"bill_no": "REF12345", "total": 500.0, "branch": "สระบุรี", "purpose": "reference"}}


def test_a_cheque_naming_an_owed_purchase_bill_pays_it():
    out = ac.build([_row("น้ำมันคอม 2 ถัง Castrol", "บริษัท พีซี เคมีคอล", 45582.0, "#PC6906-081", no=17, cheque_date=date(2026, 7, 26)),
                    _row("น้ำมันคอม 1 ถัง", "บริษัท พีซี เคมีคอล", 500.0, "#REF12345", no=18)],
                   branch="สระบุรี", file_month=date(2026, 7, 1), batch="cheque-2026-07-สระบุรี", purchases=PURCHASES)
    paid, ref = out["entries"]
    assert paid["kind"] == "pays_bill" and paid["source_id"] == "cheque-2026-07-สระบุรี:r17"
    assert {ln["account"]: ln["debit"] for ln in paid["lines"] if ln["debit"]} == {"2100": 45582.0}   # not 5500 again
    assert "PC6906-081" in paid["description"] and paid["entry_date"] == "2026-07-26"
    assert out["matched"] == [{"row": "r17", "purchase": "PC6906-081", "period": None}]             # not PC6906-08 (inside it)
    assert ref["kind"] == "cost"                                                                     # a reference bill is not owed


def test_a_cheque_bigger_than_the_bill_books_the_rest_as_cost():
    out = ac.build([_row("น้ำมันคอม 3 ถัง", "บริษัท พีซี", 50582.0, "#PC6906-081")], branch="สระบุรี", file_month=date(2026, 7, 1),
                   batch="t", purchases=PURCHASES)
    d = {ln["account"]: ln["debit"] for ln in out["entries"][0]["lines"] if ln["debit"]}
    assert d["2100"] == 45582.0 and round(sum(d.values()), 2) == 50582.0 and "5500" in d
    assert any("ส่วนเกิน" in f for f in out["flags"])


def test_printed_total_and_branch_of_a_sheet():
    rows = [["รายการจ่ายเช็ค บจ.สระบุรีวารีเทพ  (สาขาแก่งคอย)  ประจำเดือน  มกราคม  2569"],
            [4.0, "คืนเงินเหรียญบางใหญ่ (เอาเหรียญไปรวมเข้าโรงกษาปณ์)", "เงินสด", "", 90000.0],
            ["รวม", "", "", 0.0, 2923190.6]]
    assert ac.printed_total(rows) == 2923190.6                       # not the item row that has "รวม" in its text
    assert ac.sheet_branch("บจ.สระบุรี(สาขาแก่งคอย)09-68", rows) == "แก่งคอย"
    assert ac.sheet_branch("บจ.สระบุรี11-69", [["รายการจ่ายเช็ค บจ.สระบุรีวารีเทพ ประจำเดือน พฤศจิกายน 2569"]]) == "สระบุรี"
    assert ac.printed_total([[1.0, "x", "", "", 5.0]]) is None


# ------------------------------------------------------------------ month signals
def test_month_signals():
    assert rc.sheet_suffix_month("บจ.สระบุรี(สาขาแก่งคอย)11-69") == "2026-11"
    assert rc.sheet_suffix_month("บจ.สระบุรี09-68") == "2025-09"
    assert rc.file_name_month("11.ใบสรุปจ่ายเช็คประจำเดือน11-69-สระบุรี+แก่งคอย.xls") == "2026-11"
    assert rc.file_name_month("8.ใบสรุปจ่ายเช็คประจำเดือน03-69-สระบุรี+แก่งคอย.xls") == "2026-03"
    ds = [date(2026, 11, 13), date(2026, 11, 20), date(2026, 11, 20), date(2026, 12, 7), date(2026, 10, 30), date(2025, 12, 22)]
    assert rc.cheque_date_month(ds + [None]) == "2026-11"
    rows = [_row("ค่าไฟฟ้า 72/เดือน 10/69"), _row("ประกันสังคม  10/2569"), _row("คชจ.+เบิกกลางเดือน 11/2569"), _row("คชจ.+เบิกกลางเดือน 11/2569")]
    assert rc.row_tag_month(rows) == "2026-11"                       # funding rows (the run month itself) left out
    assert rc.row_tag_month([_row("เกลือ")]) is None


# ------------------------------------------------------------------ the whole workbook
HEAD = ["ที่", "รายการจ่าย", "ชื่อในเช็ค", "จำนวนเงิน", "", "วันที่บนเช็ค", "เลขที่เช็ค", "เช็คธนาคาร / สาขา", "ผู้รับเช็ค", "ว/ด/ป", "ผู้จ่าย", "หมายเหตุ"]


def _xlsx(sheets: dict) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _workbook(srb_name="บจ.สระบุรี11-69", kk_name="บจ.สระบุรี(สาขาแก่งคอย)11-69", srb_total=1393980.25):
    srb = [["รายการจ่ายเช็ค บจ.สระบุรีวารีเทพ ประจำเดือน พฤศจิกายน  2569"], HEAD, ["", "", "", "ที่อื่น", "คชจ.ตรง"],
           [1, "คชจ.+เบิกกลางเดือน 11/2569", "คุณรัชนิดาถอนจากเล่ม-โรบินสัน", "", 486647, "", "", "", "", "", "", ""],
           [2, "ค่าไฟฟ้า 72/9เดือน10/69", "ซื้อแคชเชียร์เช็คไปจ่ายที่ไฟฟ้า", "", 835380.25, "20/11/2569", "10207191", "", "", "", "", ""],
           [3, "GPS 11/69=3421.56", "เงินสด", "", 3422, 41653, "10207192", "", "", "", "", ""],
           [4, "น้ำมันคอม 2 ถัง Castrol", "บริษัท พีซี เคมีคอล", "", 45582, "20/11/2569", "10207193", "", "", "", "", "#PC6906-081"],
           [5, "ปฏิทินแจกลูกค้า 1500ชุด", "บจ. ปฏิทิน", "", 22949, "25/11/2569", "10207194", "", "", "", "", ""],
           ["รวม", "", "", 0, srb_total]]
    kk = [["รายการจ่ายเช็ค บจ.สระบุรีวารีเทพ  (สาขาแก่งคอย)  ประจำเดือน  พฤศจิกายน  2569"], HEAD, ["", "", "", "ที่อื่น", "คชจ.ตรง"],
          [1, "เงินเดือนผู้บริหาร 10/2569(แก่งคอยจ่าย)", "เงินสด", "", 185000, "20/11/2569", "10201972", "", "", "", "", ""],
          [2, "ไฟฟ้าแก่งคอย 10-69", "การไฟฟ้าส่วนภูมิภาค", "", 2246162.05, "20/11/2569", "10201974", "", "", "", "", ""],
          [3, "สระบุรียืมแก่งคอย", "บจก.สระบุรีวารีเทพ", "", 50000, "26/11/2569", "10201975", "", "", "", "", ""],
          ["รวม", "", "", "", 2481162.05]]
    return _xlsx({srb_name: srb, kk_name: kk})


REFS = ({835380.28: ("318011438503", "2026-10"), 2246162.05: ("318011438490", "2026-10")}, PURCHASES)
NAME = "11.ใบสรุปจ่ายเช็คประจำเดือน11-69-สระบุรี+แก่งคอย.xlsx"


def test_sniff_claims_hq_workbooks_only():
    assert rc.sniff(ic.sniff_file(NAME, _workbook())) >= 0.9
    assert rc.sniff(ic.sniff_file("x.xlsx", _xlsx({"Sheet1": [["ชื่อ", "ยอด"], ["a", 1]]}))) == 0.0
    pdf = ic.Dropped(name="10.ใบสรุปจ่ายเช็คประจำเดือน10-69-สระบุรี.pdf", data=b"", ext=".pdf", text="ผู้รับเช็ค\nผู้จ่าย\nคชจ.ตรง\n1 x\n20/10/2569")
    assert rc.sniff(pdf) == 0.7
    p = rc.read(pdf)
    assert p["store_only"] and p["month"] == "2026-10" and not p["batches"]


def test_read_both_branches_roles_totals_and_month():
    p = rc.read(ic.sniff_file(NAME, _workbook()), refs=REFS)
    assert p["month"] == "2026-11" and not p["conflicts"]
    assert {s["source"] for s in p["signals"] if s["what"] == "month"} == \
        {"หัวกระดาษ", "วันที่บนเช็ค", "เดือนในรายการ +1", "ท้ายชื่อชีท", "ชื่อไฟล์", "ใบแจ้ง PEA ที่จ่าย +1"}
    srb, kk = p["batches"]
    assert (srb["key"], kk["key"]) == ("cheque-2026-11-สระบุรี", "cheque-2026-11-แก่งคอย")    # the legacy importer's keys
    assert [r["role"] for r in srb["rows"]] == ["funding", "pays_bill", "cost", "pays_bill", "cost"]          # the calendars: marketing 6500 since 26 Nov 2026
    assert [r["role"] for r in kk["rows"]] == ["wages", "pays_bill", "transfer"]
    assert srb["rows_total"] == srb["file_total"] == 1393980.25
    assert kk["rows_total"] == kk["file_total"] == 2481162.05
    gps = srb["rows"][2]
    assert "41653" in gps["flag"] and gps["date"] == "2026-11-25"      # the junk date is shown, never used (the row says 11/69)
    for b in p["batches"]:
        for e in b["entries"]:
            assert round(sum(ln["debit"] for ln in e["lines"]), 2) == round(sum(ln["credit"] for ln in e["lines"]), 2)
        assert all(e["source_id"].startswith(b["key"] + ":") for e in b["entries"])


def test_a_total_that_misses_a_row_is_flagged():
    p = rc.read(ic.sniff_file(NAME, _workbook(srb_total=1371031.25)), refs=REFS)
    assert p["batches"][0]["file_total"] == 1371031.25 and p["batches"][0]["rows_total"] == 1393980.25
    assert any("ยอดรวมที่พิมพ์" in f and "22,949.00" in f for f in p["flags"])


def test_stale_sheet_names_make_a_conflict_the_owner_answers():
    data = _workbook(srb_name="บจ.สระบุรี09-68", kk_name="บจ.สระบุรี(สาขาแก่งคอย)09-68")
    p = rc.read(ic.sniff_file("8.ใบสรุปจ่ายเช็คประจำเดือน03-69-สระบุรี+แก่งคอย.xlsx", data), refs=REFS)
    assert p["month"] is None and p["conflicts"][0]["key"] == "month" and p["conflicts"][0]["default"] == "2026-11"
    assert {o["value"] for o in p["conflicts"][0]["options"]} == {"2026-11", "2025-09", "2026-03"}
    assert p["batches"][0]["key"] == "cheque-2026-11-สระบุรี"          # the preview is still shown with the likely month
    p = rc.read(ic.sniff_file("x.xlsx", data, choices={"month": "2026-11"}), refs=REFS)
    assert p["month"] == "2026-11" and not p["conflicts"]


def test_book_replaces_the_batch(monkeypatch):
    calls = []
    monkeypatch.setattr(supabase, "delete", lambda table, match: calls.append(("delete", table, match)))
    monkeypatch.setattr(supabase, "insert", lambda table, rows, **kw: calls.append(("insert", table, len(rows))) or rows)
    from app import accounting
    monkeypatch.setattr(accounting, "_require", lambda: None)
    p = rc.read(ic.sniff_file(NAME, _workbook()), refs=REFS)
    out = rc.book(p, "file-1")
    assert out["batches"] == ["cheque-2026-11-สระบุรี", "cheque-2026-11-แก่งคอย"]
    assert out["booked"] == round(1393980.25 + 2481162.05, 2)
    deletes = [c[2]["source_id"] for c in calls if c[0] == "delete"]
    assert "like.cheque-2026-11-สระบุรี:*" in deletes and "eq.cheque-2026-11-แก่งคอย" in deletes
    first_insert = next(i for i, c in enumerate(calls) if c[0] == "insert")
    assert all(c[0] == "delete" for c in calls[:first_insert])           # delete first, then insert: never doubled


def test_import_file_and_bytes_entry_points_agree(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "load_refs", lambda: REFS)
    data = _workbook()
    path = tmp_path / "x.xlsx"
    path.write_bytes(data)
    a = ac.import_file(str(path), None, "แก่งคอย")
    b = ac.import_bytes(data, "x.xlsx", None, "แก่งคอย")
    assert a["month"] == b["month"] == date(2026, 11, 1) and a["batch"] == "cheque-2026-11-แก่งคอย"
    assert [e["lines"] for e in a["entries"]] == [e["lines"] for e in b["entries"]]


# ------------------------------------------------------------------ PEA bills
def _fixture(name):
    return open(os.path.join(os.path.dirname(__file__), "fixtures", name), encoding="utf-8").read()


def _pea(monkeypatch, text, history=None, name="Feb.pdf"):
    monkeypatch.setattr(pea_bill, "parse_pdf", lambda data: pea_bill.parse_text(text))
    monkeypatch.setattr(supabase, "select", lambda table, params: [{"branch": history}] if history else [])
    return ic.Dropped(name=name, data=b"%PDF-", ext=".pdf", text=text, pages=1)


def test_pea_bill_preview(monkeypatch):
    f = _pea(monkeypatch, _fixture("pea_kk_2026_02.txt"), history="แก่งคอย")
    assert rp.sniff(f) == 0.98
    p = rp.read(f)
    assert p["month"] == "2026-02" and not p["conflicts"]
    (b,) = p["batches"]
    assert (b["key"], b["feed"], b["source"], b["branch"], b["month"]) == ("pea:312510999861", "electricity", "electricity", "แก่งคอย", "2026-02")
    assert [r["role"] for r in b["rows"]] == ["cost", "revenue"]          # the deposit interest PEA took off this bill
    assert b["rows_total"] == b["file_total"] == 1938139.34
    assert b["entries"][0]["entry_date"] == "2026-02-25" and b["entries"][0]["source_id"] == b["key"]
    import json
    json.dumps(p)                                                           # stored as jsonb: no date objects


def test_pea_branch_from_history_address_or_the_owner(monkeypatch):
    text = _fixture("pea_kk_2026_02.txt")
    addr = rp.address_branch(text)
    assert addr in (None, "แก่งคอย")
    assert rp.address_branch("สถานที่ใช้ไฟฟ้า 72/9 ม.8 ต.ตลิ่งชัน อ.เมือง จ.สระบุรี 18000") == "สระบุรี"
    assert rp.address_branch("การไฟฟ้าส่วนภูมิภาคอำเภอแก่งคอย\nสถานที่ใช้ไฟฟ้า 72 ม.8 ต.ตลิ่งชัน 18000") == "สระบุรี"
    no_addr = "\n".join(ln for ln in text.splitlines() if "18110" not in ln and "แก่งคอย" not in ln and "บ้านป่า" not in ln)
    p = rp.read(_pea(monkeypatch, no_addr))                                  # a new meter, no address: ask
    assert p["conflicts"][0]["key"] == "branch"
    f = _pea(monkeypatch, no_addr)
    f.choices = {"branch": "แก่งคอย"}                                        # the owner's answer
    p = rp.read(f)
    assert not p["conflicts"] and p["batches"][0]["branch"] == "แก่งคอย"
    wrong = no_addr.replace("Address", "สถานที่ใช้ไฟฟ้า 72 ม.8 ต.ตลิ่งชัน จ.สระบุรี 18000\nAddress", 1)
    p = rp.read(_pea(monkeypatch, wrong, history="แก่งคอย"))                  # the meter's history and the address disagree
    assert p["conflicts"][0]["key"] == "branch" and p["conflicts"][0]["default"] == "แก่งคอย"


def test_pea_book_replaces_and_asks_for_the_next_cheque_run(monkeypatch):
    from app import electricity
    saved, deleted = [], []
    f = _pea(monkeypatch, _fixture("pea_kk_2026_02.txt"), history="แก่งคอย")
    p = rp.read(f)
    monkeypatch.setattr(electricity, "save_bill", lambda bill, branch, file_id=None: saved.append((bill["invoice_no"], branch, file_id)))
    out = rp.book(p, "inbox-file-1")
    assert saved == [("312510999861", "แก่งคอย", None)]
    assert out == {"batches": ["pea:312510999861"], "booked": 1941337.18, "rebook": [{"kind": "cheques", "month": "2026-03"}]}
    monkeypatch.setattr(supabase, "delete", lambda table, match: deleted.append((table, match)))
    rp.unbook(p)
    assert deleted == [("elec_bills", {"invoice_no": "eq.312510999861"}), ("acc_entries", {"source_id": "eq.pea:312510999861"})]


def test_a_pdf_that_is_not_a_pea_bill():
    f = ic.Dropped(name="x.pdf", data=b"%PDF-", ext=".pdf", text="ใบเสร็จรับเงิน ร้านอะไหล่", pages=1)
    assert rp.sniff(f) == 0.0


# ------------------------------------------------------------------ PEA AMR
AMR = """<td colspan='5' class='header'>[ประจำเดือน : ตุลาคม 2569]</td></tr>
<tr><td class='detail'>บัญชีผู้ใช้ไฟ : </td><td>020019229331&nbsp;</td><td class='detail'>ชื่อผู้ใช้ไฟ : </td><td>x</td><td></td></tr>
<tr><td class='detail'>หมายเลขมิเตอร์ : </td><td>27666994&nbsp;</td></tr> รายงานข้อมูลกิโลวัตต์ชั่วโมงรายเดือน AMR
<tr><td class='leftcenter'>&nbsp;01/10/2026 00.15</td><td class='rightdetail'></td><td class='rightdetail'>106.43</td><td class='rightdetail'></td><td class='rightdetail'>106.43</td></tr>
<tr><td class='leftcenter'>&nbsp;01/10/2026 09.15</td><td class='rightdetail'>80</td><td class='rightdetail'></td><td class='rightdetail'></td><td class='rightdetail'>80</td></tr>
"""


def test_amr_report_is_read_and_saved_without_ledger_rows(monkeypatch):
    from app import amr
    monkeypatch.setattr(supabase, "select", lambda table, params: [{"branch": "แก่งคอย"}])
    f = ic.sniff_file("kWh02001922933103112026215412.xls", AMR.encode("utf-8"))
    assert f.sheets is None and rp.sniff(f) == 0.95
    p = rp.read(f)
    assert p["month"] == "2026-10" and p["batches"] == [] and not p["store_only"] and p["amr"]["branch"] == "แก่งคอย"
    got = []
    monkeypatch.setattr(amr, "save", lambda rep, branch: got.append((rep["ca_no"], rep["month"], len(rep["rows"]), branch)))
    assert rp.book(p, "f") == {"batches": [], "booked": 0.0, "rebook": []}
    assert got == [("020019229331", "2026-10", 2, "แก่งคอย")]
    empty = rp.read(ic.sniff_file("kWh1.xls", "รายงานข้อมูลกิโลวัตต์ชั่วโมงรายเดือน AMR [ประจำเดือน : ]".encode("utf-8")))
    assert empty["store_only"] and empty["month"] is None


@pytest.mark.parametrize("mod", [rc, rp])
def test_reader_module_shape(mod):
    assert mod.KIND and mod.LABEL and mod.FEEDS and callable(mod.sniff) and callable(mod.read) and callable(mod.book) and callable(mod.unbook)


# ------------------------------------------------------------------ AI-read bills paid by the HQ cheque (review 26 Nov 2026)
def _ai_bill(sid="bill:2026-10:แก่งคอย:ab12cd34:1", total=5320.5, party="โครงการชลประทานสระบุรี", doc_no="W-123", paid_by=None):
    return {"ai_bill": True, "source_id": sid, "month": sid.split(":")[1], "branch": "แก่งคอย", "party": party, "what": "ค่าน้ำชลประทาน ต.ค. 69",
            "doc_no": doc_no, "total": total, "paid_by": paid_by or []}


def _with_ai(*bills):
    return {**PURCHASES, **{ac.AI_KEY + b["source_id"]: b for b in bills}}


def test_a_cheque_pays_an_ai_read_bill_by_amount_and_party():
    b = _ai_bill()
    out = ac.build([_row("ค่าน้ำชลประทาน 10/69", "โครงการส่งน้ำและบำรุงรักษา", 5320.5, no=7, cheque_date=date(2026, 11, 20)),
                    _row("ค่าน้ำชลประทาน 10/69", "โครงการส่งน้ำและบำรุงรักษา", 5320.5, no=8)],
                   branch="แก่งคอย", file_month=date(2026, 11, 1), batch="cheque-2026-11-แก่งคอย", purchases=_with_ai(b))
    paid, second = out["entries"]
    assert paid["kind"] == "pays_bill" and paid["entry_date"] == "2026-11-20"
    assert {ln["account"]: ln["debit"] for ln in paid["lines"] if ln["debit"]} == {"2100": 5320.5}       # not 5200 a second time
    assert f"อ้างอิง {b['source_id']}" in paid["description"]
    assert out["matched"] == [{"row": "r7", "bill": b["source_id"], "period": "2026-10"}]
    assert second["kind"] == "cost"                                                                      # one bill is paid once
    from app import acc_checks
    assert acc_checks.ai_bills_paid(paid) == [b["source_id"]]


def test_an_ai_bill_is_not_paid_by_a_stranger_another_run_or_an_old_month():
    b = _ai_bill()
    run = dict(branch="แก่งคอย", file_month=date(2026, 11, 1), batch="cheque-2026-11-แก่งคอย")
    stranger = ac.build([_row("ซ่อมปั๊มน้ำ", "ร้านช่างแดง", 5320.5)], purchases=_with_ai(b), **run)
    assert stranger["entries"][0]["kind"] == "cost" and not stranger["matched"]                          # same amount, someone else
    paid_elsewhere = _ai_bill(paid_by=["cheque-2026-10-แก่งคอย"])
    row = _row("ค่าน้ำชลประทาน 10/69", "โครงการชลประทาน", 5320.5)
    assert ac.build([row], purchases=_with_ai(paid_elsewhere), **run)["entries"][0]["kind"] == "cost"
    paid_here = _ai_bill(paid_by=["cheque-2026-11-แก่งคอย"])                                            # this run, read again
    assert ac.build([row], purchases=_with_ai(paid_here), **run)["entries"][0]["kind"] == "pays_bill"
    old = _ai_bill(sid="bill:2026-06:แก่งคอย:ab12cd34:1")
    assert ac.build([row], purchases=_with_ai(old), **run)["entries"][0]["kind"] == "cost"
    other = dict(run, branch="สระบุรี", batch="cheque-2026-11-สระบุรี")                               # Kaeng Khoi's bill, Saraburi's sheet
    assert ac.build([row], purchases=_with_ai(b), **other)["entries"][0]["kind"] == "cost"


def test_the_bill_number_in_the_cheque_note_pays_an_ai_bill():
    b = _ai_bill(doc_no="INV-77881", total=12000.0, party="หจก. ไทยเคมี")
    out = ac.build([_row("สารกรอง", "บจ. อื่น", 10000.0, "#INV-77881")], branch="แก่งคอย", file_month=date(2026, 11, 1),
                   batch="t", purchases=_with_ai(b))
    e = out["entries"][0]
    assert e["kind"] == "pays_bill" and {ln["account"]: ln["debit"] for ln in e["lines"] if ln["debit"]} == {"2100": 10000.0}
    assert any("ยังค้าง 2,000.00" in f for f in out["flags"])
    assert ac.purchase_refs(_row("x", note="#INV-77881"), _with_ai(b)) == []                         # an AI bill is never a purchase bill


def test_load_refs_reads_open_ai_bills_and_who_pays_them(monkeypatch):
    def fake(table, params, page=1000):
        if table in ("elec_bills", "purchase_bills"):
            return []
        assert table == "acc_entries"
        if params.get("source_id") == "like.bill:*":
            return [{"id": "e1", "branch": "แก่งคอย", "source_id": "bill:2026-10:แก่งคอย:ab12cd34:1", "party": "ชลประทาน",
                     "description": "ค่าน้ำ ต.ค.", "doc_no": "W-1", "lines": [{"account": "5200", "credit": 0}, {"account": "2100", "credit": 5320.5}]},
                    {"id": "e2", "branch": "สระบุรี", "source_id": "bill:2026-10:สระบุรี:ffff0000:1", "party": "x", "description": "",
                     "doc_no": None, "lines": [{"account": "5200", "credit": 0}]}]                     # owes nothing on 2100
        assert params["source"] == "eq.hq_cheques" and params["description"].startswith("like.*อ้างอิง")
        return [{"source_id": "cheque-2026-11-แก่งคอย:r7", "description": "ค่าน้ำ · จ่ายบิล W-1 (อ้างอิง bill:2026-10:แก่งคอย:ab12cd34:1) · เช็ค 1"}]
    monkeypatch.setattr(supabase, "select_all", fake)
    bills, purchases = ac.load_refs()
    ai = ac.ai_bills_of(purchases)
    assert len(ai) == 1 and ai[0]["total"] == 5320.5 and ai[0]["month"] == "2026-10"
    assert ai[0]["paid_by"] == ["cheque-2026-11-แก่งคอย"]


def test_the_drop_box_cheque_reader_turns_the_row_into_pays_bill():
    b = _ai_bill(sid="bill:2026-10:สระบุรี:ab12cd34:1", total=22949.0, party="บจ. ปฏิทิน", doc_no="")
    b["branch"] = "สระบุรี"
    p = rc.read(ic.sniff_file(NAME, _workbook()), refs=(REFS[0], _with_ai(b)))
    srb = p["batches"][0]
    assert [r["role"] for r in srb["rows"]] == ["funding", "pays_bill", "cost", "pays_bill", "pays_bill"]
    assert {"row": "r5", "bill": b["source_id"], "period": "2026-10"} in srb["matched"]


def test_a_month_the_owner_picks_is_used_when_the_file_names_none():
    """No title, no dates, no tags: the reader asks; once the owner picks a month it reads with that month (review, 26 Nov 2026)."""
    from app import inbox_common as ic, inbox_r_cheques as rc
    import inspect
    src = inspect.getsource(rc.read)
    assert 'f.choices.get("month")' in src
