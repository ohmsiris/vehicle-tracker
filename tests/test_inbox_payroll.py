"""The drop box's payroll reader (app/inbox_r_payroll.py): what a payroll file is, its group / branch / month from the content, the preview
rows and checks, and booking that replaces (never adds). Supabase is faked; one test reads a real HQ summary when the sample folder is there."""
import os

import pytest

from app import inbox_r_payroll as R
from app import payroll_people, supabase
from app.inbox_common import Dropped, sniff_file

SUMMARY_HEAD = "เงินเดือนจัดส่ง บจ. สระบุรีวารีเทพ\nจัดส่ง\n01/11/2569\nตุลาคม 2569\nวัน\nเงิน\nซอง%"
BOX = "\nรวมรับ\nรวมจ่าย\nคงเหลือ\nBank 2 รายการ\nสด 0 รายการ\nสรุปรายหัก คืนบัญชี\nรวม"


def _person(code, gross, ded, name="นายทดสอบ ใจดี", deductions=None):
    deductions = deductions or {"advance": ded}
    return {"code": code, "name": name, "dept": "ขับรถ", "days": 30.0, "gross": gross, "deduct_total": ded, "net": gross - ded,
            "parts": {"wage": gross}, "refund": 0, "deductions": {k: float(deductions.get(k, 0)) for k in R.DED_KEYS},
            "source": "pdf", "flags": [], "details": {}}


def _drv_pdf(monkeypatch, header=SUMMARY_HEAD, people=None, extras=None, total=(30000.0, 5000.0, 25000.0), text_extra=""):
    """A drivers' summary as the reader sees it: the PDF parts and the per-person rows are faked."""
    people = people if people is not None else [_person("SB-PD001", 18000.0, 3000.0), _person("SB-PD002", 12000.0, 2000.0)]
    g, d, n = total
    # the total row then the 8 deduction columns (acc_payroll.parse_summary finds them by arithmetic)
    nums = [f"{g:,.0f}", f"{d:,.0f}", f"{n:,.0f}", "-", f"{d:,.0f}", "-", "-", "-", "-", "-", "-"]
    lines = (header + "\n" + "\n".join(p["code"] for p in people) + text_extra + BOX + "\n" + "\n".join(nums)).split("\n")
    text = "\n".join(lines)
    monkeypatch.setattr(R, "_pdf_parts", lambda data: {"text": text, "header": header, "lines": lines})
    monkeypatch.setattr(payroll_people, "parse_bytes", lambda grp, data, name, month: {"people": people, "extras": extras or [], "total": None})
    return Dropped(name="ไฟล์.pdf", data=b"%PDF-", ext=".pdf", text=text, pages=2)


def _db(monkeypatch, employees=None, booked=None):
    employees = employees if employees is not None else [{"id": "e1", "code": "SB-PD001", "branch": "สระบุรี"},
                                                         {"id": "e2", "code": "SB-PD002", "branch": "สระบุรี"}]
    monkeypatch.setattr(supabase, "configured", lambda: True)

    def select_all(table, params, page=1000):
        if table == "employees":
            return employees
        if table == "acc_entries":
            return booked or []
        raise AssertionError(table)
    monkeypatch.setattr(supabase, "select_all", select_all)


# ------------------------------------------------------------------ what the file is
def test_the_box_makes_a_summary_whatever_the_title_says():
    stale = "ใบเซ็นต์รับสลิปจัดส่ง บจ. สระบุรีวารีเทพ\nSB-PD001" + BOX
    assert R.doc_type(stale) == "summary"
    assert R.stale_title("ใบเซ็นต์รับสลิปจัดส่ง บจ. สระบุรีวารีเทพ") == "ใบเซ็นรับสลิป"
    assert R.doc_type("บจ. สระบุรีวารีเทพ สาขาแก่งคอย\nรายการหัก\nเข้า BANK 8 รายการ") == "summary"
    assert R.doc_type("บริษัท สระบุรีวารีเทพ จำกัด\nสลิปเงินเดือน\nประจำเดือน :\nรายการหัก") == "slip"
    assert R.doc_type("รายชื่อรับเงินสดช่าง+ออฟฟิศ+ฝ่ายผลิต\nรวมสด เฉพาะค่าแรง") == "cash"
    assert R.doc_type("พขร.-โอนเข้าบัญชี มกราคม 2569 (เซ็นรับสลิป)") == "sign"
    assert R.doc_type("ที่ scan no. รายชื่อ จำนวนเงิน ลงชื่อ") == "sign"          # a sign sheet with no title left
    assert R.doc_type("ที่ Scan รายชื่อ จำนวนเงิน หมายเหตุ") == "cash"            # the Kaeng Khoi cash list


def test_sniff_claims_payroll_pdfs_by_content_and_leaves_scans():
    codes = "\n".join(f"SB-FR{i:03d}" for i in range(1, 6))
    assert R.sniff(Dropped(name="a.pdf", data=b"", ext=".pdf", text="บจ. ช่าง+ออฟฟิศ\n" + codes + BOX)) == 0.95
    assert R.sniff(Dropped(name="a.pdf", data=b"", ext=".pdf", text="สลิปเงินเดือน ประจำเดือน\n" + codes)) == 0.9
    assert R.sniff(Dropped(name="สรุปเงินเดือน พขร.pdf", data=b"", ext=".pdf", text="ใบแจ้งหนี้ค่าไฟฟ้า")) == 0.0      # the name never decides
    assert R.sniff(Dropped(name="a.pdf", data=b"", ext=".pdf", is_scan=True)) == 0.0
    sheets = {"สลิป": [["SB-FR001"]], "ตารางรายรับ-จ่ายพนง.": [["scan no.", "รายชื่อ", "รวมรับ", "รวมจ่าย", "คงเหลือ"]] +
              [[f"SB-FR00{i}", "x", 1, 0, 1] for i in range(1, 5)]}
    assert R.sniff(Dropped(name="a.xlsx", data=b"", ext=".xlsx", sheets=sheets)) == 0.95
    assert R.sniff(Dropped(name="Drivers.xlsx", data=b"", ext=".xlsx", sheets={"Driver Sales 2026": [["SB-PD001", 5]] * 5})) == 0.0


def test_group_from_title_and_codes():
    assert R.group_of_title("บจ. สระบุรีวารีเทพ สาขาแก่งคอย เงินเดือนพนักงาน") == "KK"
    assert R.group_of_title("บจ. สระบุรีวารีเทพ รับเงินสด ฝ่ายผลิต เดือน ตุลาคม 2569") == "FAC"
    assert R.group_of_title("เงินเดือนจัดส่ง บจ. สระบุรีวารีเทพ") == "DRV"
    assert R.group_of_codes(["SB-PD001", "SB-PD002", "SB-FR001"]) == "DRV"
    assert R.branch_of_title("บจ. สระบุรีวารีเทพ สาขาแก่งคอย") == "แก่งคอย" and R.branch_of_title("บจ. สระบุรีวารีเทพ") == "สระบุรี"


# ------------------------------------------------------------------ months
def test_months_from_the_words_typos_and_fonts():
    assert R.title_month("เงินเดือนจัดส่ง พฤภาคม 2569") == "2026-05"             # HQ's typo in the May drivers' sheet
    assert R.title_month("ประจำเดือน : กรกฎำคม 2569") == "2026-07"                # a font that prints า as ำ
    assert R.title_month("ช่าง+ออฟฟิศ เดือน มกราคม2569") == "2026-01"
    assert R.title_month("เงินเดือน") is None
    assert R.period_month("คืนสำรองค่าใช้จ่าย11-25/10/2569\nค่าใช้จ่าย 11-25/10/69") == "2026-10"
    assert R.period_month("งวด 26/09/2569 - 25/10/2569") == "2026-10"
    assert R.printed_month("จัดส่ง\n01/11/2569\n") == "2026-10"                    # printed after the 25th of October
    assert R.printed_month("28/10/2569") == "2026-10"
    assert R.name_month("สรุป-เงินเดือนแก่งคอย 02-69.pdf") == "2026-02"
    assert R.name_month("เงินเดือนแก่งคอย (แก้ไข01-69.xlsx") == "2026-01"
    assert R.name_month("ใบสรุป-เงินเดือนแก่งคอย มกราคม.69.pdf") == "2026-01"


# ------------------------------------------------------------------ read: a clean summary
def test_a_clean_summary_previews_one_batch_with_roles_and_checks(monkeypatch):
    _db(monkeypatch)
    f = _drv_pdf(monkeypatch)
    p = R.read(f)
    assert p["kind"] == "payroll" and not p["store_only"] and p["conflicts"] == []
    assert p["month"] == "2026-10" and p["group"] == "DRV"
    (b,) = p["batches"]
    assert b["key"] == "payroll:2026-10:DRV" and b["feed"] == "hq_salary" and b["branch"] == "สระบุรี"
    assert b["file_total"] == 30000 and b["rows_total"] == 30000
    assert [r["role"] for r in b["rows"]] == ["wages", "wages", "advance"]
    assert b["rows"][-1]["in_total"] is False
    (e,) = b["entries"]
    assert e["source_id"] == "payroll:2026-10:DRV" and e["entry_date"] == "2026-10-25"
    assert sum(ln["debit"] for ln in e["lines"]) == sum(ln["credit"] for ln in e["lines"]) == 30000
    assert {ln["account"] for ln in e["lines"] if ln["debit"]} == {"6200"}
    assert all(c["ok"] for c in b["checks"]), b["checks"]
    pm = b["pay_months"]
    assert pm["month"] == "2026-10" and pm["grp"] == "drivers" and [r["employee_id"] for r in pm["rows"]] == ["e1", "e2"]
    assert p["people"] == {"count": 2, "matched": 2, "unmatched": []}
    assert {s["what"] for s in p["signals"]} == {"group", "month", "branch"}


def test_petty_cash_pay_back_is_funding_not_wages(monkeypatch):
    _db(monkeypatch)
    extras = [{"label": "คืนสำรองค่าใช้จ่าย11-25/10/2569", "gross": 10000.0, "deduct_total": 0.0, "net": 10000.0}]
    f = _drv_pdf(monkeypatch, extras=extras, total=(40000.0, 5000.0, 35000.0),
                 text_extra="\nคืนสำรองค่าใช้จ่าย11-25/10/2569\n10,000\n-\n10,000")
    p = R.read(f)
    (b,) = p["batches"]
    funding = [r for r in b["rows"] if r["role"] == "funding"]
    assert len(funding) == 1 and funding[0]["amount"] == 10000 and funding[0]["account"] == "1100"
    assert b["file_total"] == b["rows_total"] == 40000
    e = b["entries"][0]
    assert next(ln["debit"] for ln in e["lines"] if ln["account"] == "6200") == 30000      # gross less the pay-back
    assert next(ln["credit"] for ln in e["lines"] if ln["account"] == "1100") == 25000
    assert all(c["ok"] for c in b["checks"]), b["checks"]


def test_stale_title_is_flagged_and_read_as_the_summary(monkeypatch):
    _db(monkeypatch)
    f = _drv_pdf(monkeypatch, header="ใบเซ็นต์รับสลิปจัดส่ง บจ. สระบุรีวารีเทพ\n01/11/2569\nตุลาคม 2569")
    p = R.read(f)
    assert not p["store_only"] and p["batches"][0]["key"] == "payroll:2026-10:DRV"
    assert any("หัวกระดาษเขียนว่า" in x for x in p["flags"])


# ------------------------------------------------------------------ read: conflicts
def test_month_in_the_title_against_the_file_name_is_a_conflict(monkeypatch):
    _db(monkeypatch)
    f = _drv_pdf(monkeypatch)
    f.name = "สรุปเงินเดือน พขร. สระบุรี กันยายน 2569 -.pdf"                   # a stale name
    p = R.read(f)
    (c,) = p["conflicts"]
    assert c["key"] == "month" and c["default"] == "2026-10" and {o["value"] for o in c["options"]} == {"2026-10", "2026-09"}
    assert p["month"] is None
    f.choices = {"month": "2026-10"}
    assert R.read(f)["conflicts"] == [] and R.read(f)["batches"][0]["key"] == "payroll:2026-10:DRV"


def test_title_against_the_codes_is_a_group_conflict(monkeypatch):
    _db(monkeypatch, employees=[])
    f = _drv_pdf(monkeypatch, people=[_person("SB-FR001", 18000.0, 3000.0), _person("SB-FR002", 12000.0, 2000.0)])
    p = R.read(f)
    keys = {c["key"] for c in p["conflicts"]}
    assert "group" in keys
    g = next(c for c in p["conflicts"] if c["key"] == "group")
    assert {o["value"] for o in g["options"]} == {"FAC", "DRV"}


def test_branch_from_the_people_must_agree_with_the_title(monkeypatch):
    _db(monkeypatch, employees=[{"id": "e1", "code": "SB-PD001", "branch": "แก่งคอย"}, {"id": "e2", "code": "SB-PD002", "branch": "แก่งคอย"}])
    p = R.read(_drv_pdf(monkeypatch))
    b = next(c for c in p["conflicts"] if c["key"] == "branch")
    assert {o["value"] for o in b["options"]} == {"แก่งคอย", "สระบุรี"}


def test_the_same_totals_booked_under_another_month_is_a_duplicate(monkeypatch):
    booked = [{"source_id": "payroll:2026-09:DRV", "acc_lines": [{"account": "6200", "debit": 30000, "credit": 0},
                                                                 {"account": "1100", "debit": 0, "credit": 25000}]}]
    _db(monkeypatch, booked=booked)
    f = _drv_pdf(monkeypatch)
    p = R.read(f)
    d = next(c for c in p["conflicts"] if c["key"] == "duplicate")
    assert d["default"] == "store" and "ก.ย. 2569" in d["message"]
    f.choices = {"duplicate": "store"}
    p = R.read(f)
    assert p["store_only"] and p["batches"] == []
    f.choices = {"duplicate": "book"}
    p = R.read(f)
    assert not p["store_only"] and not p["conflicts"] and p["batches"][0]["key"] == "payroll:2026-10:DRV"


def test_the_same_month_already_booked_only_says_it_will_be_replaced(monkeypatch):
    booked = [{"source_id": "payroll:2026-10:DRV", "acc_lines": [{"account": "6200", "debit": 30000, "credit": 0},
                                                                 {"account": "1100", "debit": 0, "credit": 25000}]}]
    _db(monkeypatch, booked=booked)
    p = R.read(_drv_pdf(monkeypatch))
    assert p["conflicts"] == [] and any("แทนที่ของเดิม ไม่เพิ่ม" in x for x in p["flags"])


def test_people_not_adding_up_to_the_print_fail_a_check(monkeypatch):
    _db(monkeypatch)
    p = R.read(_drv_pdf(monkeypatch, people=[_person("SB-PD001", 18000.0, 3000.0)]))
    bad = [c["what"] for c in p["batches"][0]["checks"] if not c["ok"]]
    assert "รายคน + คืนเงินสดย่อย = รวมรับที่พิมพ์" in bad and any(x.startswith("ตรวจไม่ผ่าน") for x in p["flags"])


def test_without_a_database_the_summary_still_reads(monkeypatch):
    monkeypatch.setattr(supabase, "configured", lambda: False)
    p = R.read(_drv_pdf(monkeypatch))
    assert p["batches"][0]["key"] == "payroll:2026-10:DRV" and any("ต่อฐานข้อมูลไม่ได้" in x for x in p["flags"])


def test_slips_cash_lists_and_sign_sheets_are_kept_not_booked(monkeypatch):
    text = "บริษัท สระบุรีวารีเทพ จำกัด\nสลิปเงินเดือน\nประจำเดือน : ตุลาคม 2569\n" + "\n".join(f"SB-PD{i:03d}" for i in range(1, 6))
    monkeypatch.setattr(R, "_pdf_parts", lambda data: {"text": text, "header": text, "lines": text.splitlines()})
    p = R.read(Dropped(name="x.pdf", data=b"%PDF-", ext=".pdf", text=text))
    assert p["store_only"] and p["batches"] == [] and p["month"] == "2026-10" and "สลิป" in p["note"] and p["group"] == "DRV"


def _slip(monkeypatch, title_month="กรกฎาคม 2569", pay_date="01/09/2569"):
    text = (f"บริษัท สระบุรีวารีเทพ จำกัด\nสลิปเงินเดือน\nประจำเดือน : {title_month}\n"
            + "".join(f"รหัส :\nSB-PD{i:03d}\nวันที่จ่าย\n{pay_date}\n" for i in range(1, 6)))
    header = text.split("\nรหัส")[0]
    monkeypatch.setattr(R, "_pdf_parts", lambda data: {"text": text, "header": header, "lines": text.splitlines()})
    return text


def test_a_stale_slip_heading_does_not_outweigh_the_pay_date_and_the_name(monkeypatch):
    """Drivers' slips of Aug (corrected) and Sep 2569: headed July, paid 01/09 and 01/10/2569 -- filed under Aug / Sep, and said."""
    text = _slip(monkeypatch)
    p = R.read(Dropped(name="สลิป พขร. สระบุรี สิงหาคม 2569 -.pdf", data=b"%PDF-", ext=".pdf", text=text))
    assert p["store_only"] and p["batches"] == [] and p["month"] == "2026-08"
    assert p["conflicts"] and p["conflicts"][0]["key"] == "month" and p["conflicts"][0]["default"] == "2026-08"
    assert any("ก.ค. 2569" in x and "เก็บไว้กับเดือน ส.ค. 2569" in x for x in p["flags"])
    assert {s["source"]: s.get("weight") for s in p["signals"] if s["what"] == "month"} == {"หัวกระดาษ": R.W_TITLE_PAPER, "วันที่จ่าย": 1.0,
                                                                                           "ชื่อไฟล์": 0.5}
    text = _slip(monkeypatch, pay_date="01/10/2569")
    p = R.read(Dropped(name="สลิป พขร. สระบุรี กันยายน 2569 -.pdf", data=b"%PDF-", ext=".pdf", text=text))
    assert p["month"] == "2026-09"
    # the pay date alone still beats a stale heading; the heading alone still beats the file name
    text = _slip(monkeypatch)
    assert R.read(Dropped(name="x.pdf", data=b"%PDF-", ext=".pdf", text=text))["month"] == "2026-08"
    text = _slip(monkeypatch, title_month="ตุลาคม 2569", pay_date="")
    assert R.read(Dropped(name="สลิป กันยายน 2569.pdf", data=b"%PDF-", ext=".pdf", text=text))["month"] == "2026-10"


def test_a_summary_title_still_outweighs_the_print_date(monkeypatch):
    """Only the kept papers lose weight: a summary's title (3) still beats its print date (1) and name (0.5) -- then it is a conflict."""
    p = R.read(_drv_pdf(monkeypatch, header=SUMMARY_HEAD.replace("01/11/2569", "01/12/2569")))
    assert [c["key"] for c in p["conflicts"]] == ["month"] and p["conflicts"][0]["default"] == "2026-10" and p["month"] is None


REAL_SLIPS = "E:/Saraburi/ClaudeCode/CP Accounting/เงินเดือน/รายละเอียดเงินเดือน 2569/"


@pytest.mark.parametrize("path, month", [("08-แก้ไข-จัดส่ง/สลิป พขร. สระบุรี สิงหาคม 2569 -.pdf", "2026-08"),
                                         ("09/สลิป พขร. สระบุรี กันยายน 2569 -.pdf", "2026-09")])
def test_real_driver_slips_headed_july_file_under_their_month(monkeypatch, path, month):
    if not os.path.exists(REAL_SLIPS + path):
        pytest.skip("sample payroll files not on this machine")
    monkeypatch.setattr(supabase, "configured", lambda: False)
    p = R.read(sniff_file(os.path.basename(path), open(REAL_SLIPS + path, "rb").read()))
    assert p["doc"] == "slip" and p["store_only"] and p["month"] == month and p["group"] == "DRV"
    assert any("ก.ค. 2569" in x for x in p["flags"])


# ------------------------------------------------------------------ the workbook
def test_workbook_main_sheet_header_and_total_row():
    rows = [["บจ. สระบุรีวารีเทพ สาขาแก่งคอย เงินเดือนพนักงาน เดือน ตุลาคม 2569"], ["ที่", "scan", "รายชื่อ", "รวมรับ", "รวมจ่าย", "คงเหลือ"],
            [1, "SB-KK001", "ก", 10000.5, 1000, 9000.5], [None, None, "ค่าใช้จ่าย 11-25/10/69", 500, 0, 500],
            ["รวม", None, None, 10500.5, 1000, 9500.5], [None, None, "ค่าใช้จ่าย 11-25/12/68", 1, 0, 1]]
    name, main = R._main_sheet({"สลิป": [["SB-KK001"]], "สรุป": rows})
    assert name == "สรุป"
    parts = R._xlsx_parts(main)
    assert parts["total"] == {"gross": 10500.5, "deduct_total": 1000, "net": 9500.5}
    assert R.title_month(parts["header"]) == "2026-10" and R.group_of_title(parts["header"]) == "KK"
    assert R.period_month(parts["text"]) == "2026-10"                        # rows below the total row are not read
    assert R._baht(870472.5) == 870473.0                                     # half up, as Excel prints it


# ------------------------------------------------------------------ book / unbook
def test_book_replaces_the_entry_and_exactly_that_group_month_of_pay(monkeypatch):
    _db(monkeypatch)
    p = R.read(_drv_pdf(monkeypatch))
    calls = []
    monkeypatch.setattr(supabase, "delete", lambda table, match: calls.append(("delete", table, match)))
    monkeypatch.setattr(supabase, "insert", lambda table, rows, **kw: calls.append(("insert", table, len(rows))))
    from app import accounting
    monkeypatch.setattr(accounting, "_require", lambda: None)
    monkeypatch.setattr(accounting, "_insert_entries", lambda entries: calls.append(("entries", [e["source_id"] for e in entries])))
    out = R.book(p, "file-1")
    assert out == {"batches": ["payroll:2026-10:DRV"], "booked": 30000.0, "rebook": []}
    assert calls[:3] == [("delete", "acc_entries", {"source_id": "eq.payroll:2026-10:DRV"}),
                         ("delete", "acc_entries", {"source_id": "like.payroll:2026-10:DRV:*"}),
                         ("delete", "employee_pay_months", {"month": "eq.2026-10", "grp": "eq.drivers"})]
    assert calls[3:] == [("entries", ["payroll:2026-10:DRV"]), ("insert", "employee_pay_months", 2)]
    calls.clear()
    R.unbook(p)
    assert [c[1] for c in calls] == ["acc_entries", "acc_entries", "employee_pay_months"]


def test_parse_bytes_reads_through_a_temporary_file(monkeypatch, tmp_path):
    seen = {}

    def fake(path, month):
        seen["path"], seen["exists"], seen["data"] = path, os.path.exists(path), open(path, "rb").read()
        return [{"code": "SB-PD001", "flags": [], "parts": {}}]
    monkeypatch.setattr(payroll_people.drivers, "parse_file", fake)
    out = payroll_people.parse_bytes("drivers", b"abc", "x.xlsx", "2026-10")
    assert seen["exists"] and seen["data"] == b"abc" and seen["path"].endswith(".xlsx") and not os.path.exists(seen["path"])
    assert out["people"][0]["group"] == "drivers" and out["extras"] == [] and out["total"] is None


# ------------------------------------------------------------------ a real HQ summary (when the sample folder is on this machine)
REAL = "E:/Saraburi/ClaudeCode/CP Accounting/เงินเดือน/สรุป-เงินเดือนแก่งคอย ตุลาคม 2569.pdf"


@pytest.mark.skipif(not os.path.exists(REAL), reason="sample payroll files not on this machine")
def test_real_kaeng_khoi_october_summary(monkeypatch):
    monkeypatch.setattr(supabase, "configured", lambda: False)
    f = sniff_file("renamed.pdf", open(REAL, "rb").read())                  # the name says nothing: the content decides
    assert R.sniff(f) == 0.95
    p = R.read(f)
    assert (p["month"], p["group"], p["conflicts"]) == ("2026-10", "KK", [])
    (b,) = p["batches"]
    assert b["key"] == "payroll:2026-10:KK" and b["branch"] == "แก่งคอย" and b["file_total"] == 809483
    e = b["entries"][0]
    assert next(ln["debit"] for ln in e["lines"] if ln["account"] == "5400") == 598163          # = the imported entry
    assert next(ln["credit"] for ln in e["lines"] if ln["account"] == "1100") == 447658
    assert all(c["ok"] for c in b["checks"]), b["checks"]
    assert len(b["pay_months"]["rows"]) == 45 and sum(r["role"] == "funding" for r in b["rows"]) == 2
