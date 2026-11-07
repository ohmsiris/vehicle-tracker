from datetime import date

from app import acc_cheques as ac

SHEET = [
    ["รายการจ่ายเช็ค บจ.สระบุรีวารีเทพ ประจำเดือน กันยายน 2569"] + [""] * 11,
    ["ที่", "รายการจ่าย", "ชื่อในเช็ค", "จำนวนเงิน", "", "วันที่บนเช็ค", "เลขที่เช็ค", "", "", "", "", "หมายเหตุ"],
    [1.0, "คชจ.+เบิกกลางเดือน 9/2569", "คุณรัชนิดาถอนจากเล่ม", "", 404701.0, "", "", "", "", "", "", ""],
    [2.0, "ค่าไฟฟ้า 72/เดือน 08/69", "ซื้อแคชเชียร์เช็คไปจ่ายที่ไฟฟ้า", "", 107.0, 244612.0, 10207161.0, "", "", "", "", ""],
    [3.0, "ค่าทำบัญชี+ภาษี+08-69", "ธิดารัตน์", "", 96494.67, "", "10207220", "", "", "", "", "บริการ=13000/ภงด.53=455.67vat=83,039"],
    [4.0, "ถุงใส19*31", "นายสุกิจ", "", 10800.0, "", "", "", "", "", "", ""],
    [5.0, "ของแปลก", "บริษัท เอ", "", 500.0, "", "", "", "", "", "", ""],
    ["รวม", "", "", 0.0, 1.0, "", "", "", "", "", "", ""],
]


def test_be_dates():
    assert ac.be_date(244592.0) == date(2026, 8, 31)
    assert ac.be_date("15/09/2569") == date(2026, 9, 15)
    assert ac.be_date("") is None


def test_parse_and_build():
    rows = ac.parse_sheet(SHEET)
    assert [r["no"] for r in rows] == [1, 2, 3, 4, 5]
    out = ac.build(rows, branch="สระบุรี", file_month=date(2026, 9, 1), batch="t")
    e = {int(x["source_id"].split(":r")[1]): x for x in out["entries"]}
    debit = lambda n: {ln["account"]: ln["debit"] for ln in e[n]["lines"] if ln["debit"]}
    assert debit(1) == {"1100": 404701.0}                                   # petty cash funding is not a cost
    assert debit(2) == {"5100": 100.0, "1400": 7.0} and e[2]["entry_date"] == "2026-08-25"   # PEA: VAT split, usage month
    assert debit(3) == {"7600": 13000.0, "2200": 455.67, "2300": 83039.0}
    assert debit(4) == {"5330": 10800.0} and e[4]["entry_date"] == "2026-08-25"  # an individual: no VAT; last month's bill
    assert debit(5) == {"7900": 467.29, "1400": 32.71} or debit(5) == {"7900": 500.0}
    assert any("ของแปลก" in f for f in out["flags"])
    for x in out["entries"]:
        assert round(sum(ln["debit"] for ln in x["lines"]), 2) == round(sum(ln["credit"] for ln in x["lines"]), 2)


def _row(what, payee="", amount=1000.0, note="", no=1):
    return {"no": no, "what": what, "payee": payee, "amount": amount, "cheque_date": None, "cheque_no": "1", "bank": "", "note": note}


def test_rules_from_the_whole_year():
    c = ac.classify
    assert c(_row("แกนทุ่นมอร์เตอร์ ขึ้น-ลง /เอ็ม.ดี. เจริญผลรอกไฟฟ้า", "บริษัท เอ็ม.ดี."))["account"] == "5500"   # not electricity
    assert c(_row("ตรวจระบบไฟฟ้า ประจำปี", "เงินสด"))["account"] == "5500"
    assert c(_row("ไฟฟ้าแก่งคอย 08-69", "การไฟฟ้า"))["account"] == "5100"
    assert c(_row("เงินเดือนผู้บริหาร 05/2569(ยืมปราจีน)", "เงินสด"))["account"] == "7100"     # a salary, though borrowed
    assert c(_row("สระบุรียืมแก่งคอย", "บจก.สระบุรีวารีเทพ"))["kind"] == "transfer"
    assert c(_row("ค่าน้ำแข็งบางปะอิน08-2569", "พัชรินทร์"))["account"] == "5050"
    assert c(_row("ต่อภาษี+พรบ รถ 14 คัน", "คงเกียรติ์"))["account"] == "6300"
    assert c(_row("CI พม่า 14 คน/ทำพาสปอร์ต", "ธนพร"))["account"] == "7120"
    assert c(_row("งาน PM เครื่อง80ตัน", "บริษัท พัฒน์กล"))["account"] == "5500"
    assert c(_row("ผ่อนคอนโด 2/69", "เงินสด"))["account"] == "3200"           # the owner's own, not a company cost


def test_run_month_from_the_title():
    assert ac.run_month([["รายการจ่ายเช็ค บจ.สระบุรีวารีเทพ ประจำเดือน พฤษภาคม  2569"]]) == date(2026, 5, 1)


def test_accountant_note_with_the_total_as_vat():
    rows = [_row("ค่าทำบัญชี+ภาษี+02-69", "ธิดารัตน์", 104574.57, "บริการ=13000/ภงด.53=2505.57/vat=104574.57")]
    out = ac.build(rows, branch="สระบุรี", file_month=date(2026, 3, 1), batch="t")
    d = {ln["account"]: ln["debit"] for ln in out["entries"][0]["lines"] if ln["debit"]}
    assert d == {"7600": 13000.0, "2200": 2505.57, "2300": 89069.0}


def test_near_bill_match_needs_the_same_month():
    bills = {774711.32: ("318011328154", "2026-08"), 1938139.34: ("312510999861", "2026-02")}
    out = ac.build([_row("ค่าไฟฟ้า 72/9เดือน03/69", "", 774240.05),
                    _row("ไฟฟ้าแก่งคอย 02-69", "การไฟฟ้า", 1939139.34, no=2)], branch="x", file_month=date(2026, 4, 1), batch="t", bills=bills)
    assert out["entries"][0]["kind"] == "cost"                                  # March's cheque is not August's bill
    e = out["entries"][1]
    assert e["kind"] == "pays_bill" and {ln["account"]: ln["debit"] for ln in e["lines"] if ln["debit"]} == {"2100": 1938139.34, "7900": 1000.0}


def test_bank_paid_salaries_are_a_transfer_not_wages():
    assert ac.classify(_row("เงินเดือนตัดธนาคาร เข้าบจ.สระบุรี 08 -69", "บจ.สระบุรีวารีเทพ"))["kind"] == "transfer"
    assert ac.classify(_row("เงินเดือนผู้บริหาร 08/2569(แก่งคอยจ่าย)", "เงินสด"))["account"] == "7100"


def test_cash_withdrawn_from_mums_book_leaves_her_account():
    out = ac.build([_row("คชจ.+เบิกกลางเดือน 9/2569", "คุณรัชนิดาถอนจากเล่ม-โรบินสัน", 404701.0),
                    _row("น้ำมันคอม 2 ถัง", "บริษัท พี เคมีคอล ลู้บ จำกัด", 45582.0, no=2)], branch="สระบุรี", file_month=date(2026, 9, 1), batch="t")
    cr = [next(ln["account"] for ln in e["lines"] if ln["credit"]) for e in out["entries"]]
    assert cr == ["1120", "1130"]


def test_a_cheque_paid_to_mum_is_still_from_hq():
    assert ac.paid_from(_row("ค่าน้ำแข็งแก่งคอย#07/69", "รัชนิดา  วัฒนสาคร")) == "1130"
    assert ac.paid_from(_row("คชจ.+เบิกกลางเดือน 9/2569", "แก่งคอยถอนจากเล่มชื่อ K.รัชนิดา")) == "1120"
